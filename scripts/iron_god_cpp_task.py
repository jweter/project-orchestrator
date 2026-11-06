"""Run one bounded C++ task through the local builder: a local Ollama model, the real compiler and the repository's tests.

No external AI service is called. The model is only the author; scope, compilation, tests, retry limits, memory admission,
rollback and the optional independent oracle check are deterministic code (``project_orchestrator.iron_god_cpp_builder``).

    python scripts/iron_god_cpp_task.py --task task.json --out evidence.json [--model qwen2.5-coder:7b]

The task file names the isolated workspace (a git checkout), the files to write, the scope, the context and the commands.
An optional ``oracle`` runs a held-out, independently written test against the builder's result after it succeeds.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import sys
import threading
import time
import urllib.request
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
for _path in (str(_root), str(_root / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from project_orchestrator import iron_god_cpp_builder as cb  # noqa: E402

OLLAMA = "http://127.0.0.1:11434/api/generate"


class _MemoryStatus(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong), ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]


def available_gib() -> float:
    """Physical memory available to a new process (free plus reclaimable cache), in GiB."""
    status = _MemoryStatus()
    status.dwLength = ctypes.sizeof(_MemoryStatus)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))  # type: ignore[attr-defined]
    return status.ullAvailPhys / 1024**3


class Sampler(threading.Thread):
    """Records the lowest available memory seen while the task runs."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.lowest, self.stop_flag = available_gib(), threading.Event()

    def run(self) -> None:
        while not self.stop_flag.wait(2.0):
            self.lowest = min(self.lowest, available_gib())


def ollama(model: str, prompt: str, *, num_ctx: int, num_predict: int, keep_alive: str, timeout: int) -> dict:
    body = json.dumps({"model": model, "prompt": prompt, "stream": False, "keep_alive": keep_alive,
                       "options": {"temperature": 0, "num_ctx": num_ctx, "num_predict": num_predict}}).encode()
    request = urllib.request.Request(OLLAMA, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - loopback only
        return json.loads(response.read().decode("utf-8"))


def register_cmake(workspace: Path, cmake: str, name: str, source: str) -> None:
    """Deterministic scaffolding: register one test executable inside the BUILD_TESTING block of the project's CMake file."""
    path = workspace / cmake
    text = path.read_text(encoding="utf-8")
    block = (f"    add_executable({name} {source})\n    target_link_libraries({name} PRIVATE everward_simulation)\n"
             f"    add_test(NAME {name} COMMAND {name})\n")
    if f"add_executable({name} " in text:
        return
    marker = text.rindex("endif()")
    path.write_text(text[:marker] + block + text[marker:], encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="qwen2.5-coder:7b")
    parser.add_argument("--min-free-gib", type=float, default=7.0)
    parser.add_argument("--abort-below-gib", type=float, default=0.6)
    parser.add_argument("--memory-wait-minutes", type=float, default=0.0)
    parser.add_argument("--iterations", type=int, default=4)
    parser.add_argument("--model-timeout", type=int, default=900)
    parser.add_argument("--num-ctx", type=int, default=6144)
    parser.add_argument("--num-predict", type=int, default=2200)
    args = parser.parse_args(argv)
    spec = json.loads(args.task.read_text(encoding="utf-8"))
    workspace = Path(spec["workspace"])
    build_dir = Path(spec["build_dir"])
    os.environ["MSBUILDDISABLENODEREUSE"] = "1"  # leave no idle build nodes holding memory afterwards
    register = spec["cmake_register"]
    register_cmake(workspace, register["file"], register["name"], register["source"])
    cmake_source = str(workspace / Path(register["file"]).parent)
    task = cb.Task(
        id=spec["id"], objective=spec["objective"], workspace=workspace, allowed_paths=spec["allowed_paths"],
        forbidden_paths=spec.get("forbidden_paths", []), files=spec["files"], context_files=spec["context_files"],
        rules=spec.get("rules", []),
        configure=[["cmake", "-S", cmake_source, "-B", str(build_dir), "-G", "Visual Studio 18 2026", "-A", "x64",
                    "-DBUILD_TESTING=ON"]],
        build=["cmake", "--build", str(build_dir), "--config", "Debug", "--target", register["name"], "--",
               "/nodeReuse:false", "/m:2", "/v:minimal"],
        test=["ctest", "--test-dir", str(build_dir), "-C", "Debug", "-R", f"^{register['name']}$", "--output-on-failure"])
    stats: list[dict] = []

    def ask(prompt: str) -> str:
        """One model call in a worker thread, watched by a continuous memory guard that aborts and unloads on pressure."""
        outcome: dict = {}

        def call() -> None:
            try:
                outcome["reply"] = ollama(args.model, prompt, num_ctx=args.num_ctx, num_predict=args.num_predict,
                                          keep_alive="3m", timeout=args.model_timeout)
            except Exception as exc:  # noqa: BLE001 - reported to the loop as a model failure
                outcome["error"] = exc

        worker = threading.Thread(target=call, daemon=True)
        worker.start()
        guard = cb.MemoryGuard(available_gib, floor_gib=args.abort_below_gib, patience=2)
        while worker.is_alive():
            worker.join(2.0)
            if worker.is_alive() and guard.check():
                try:
                    ollama(args.model, "", num_ctx=512, num_predict=1, keep_alive="0", timeout=30)  # unload frees the memory
                except OSError:
                    pass
                raise OSError(f"MEMORY_PRESSURE_ABORT: available memory stayed under {args.abort_below_gib} GiB "
                              f"(lowest {guard.lowest:.2f} GiB); the model was unloaded")
        if "error" in outcome:
            raise outcome["error"]
        reply = outcome["reply"]
        stats.append({k: reply.get(k) for k in ("eval_count", "eval_duration", "prompt_eval_count", "prompt_eval_duration",
                                                 "load_duration", "total_duration")})
        return str(reply.get("response", ""))

    waits = max(int(args.memory_wait_minutes * 3), 1)
    limits = cb.Limits(max_iterations=args.iterations, min_free_gib=args.min_free_gib, memory_waits=waits, memory_wait_s=20.0)
    sampler = Sampler()
    sampler.start()
    started = time.time()
    evidence = cb.build_change(task, ask, limits=limits, free_gib=available_gib)
    sampler.stop_flag.set()
    evidence["model"] = args.model
    evidence["model_calls"] = stats
    evidence["tokens_per_second"] = [round(s["eval_count"] / (s["eval_duration"] / 1e9), 2) for s in stats
                                     if s.get("eval_count") and s.get("eval_duration")]
    evidence["lowest_available_gib"] = round(sampler.lowest, 2)
    evidence["wall_seconds"] = round(time.time() - started, 1)
    oracle = spec.get("oracle")
    if oracle and evidence["status"] == cb.SUCCESS:
        evidence["oracle"] = run_oracle(spec, task, register, oracle)
    try:  # release the model's memory as soon as the task is over
        ollama(args.model, "", num_ctx=512, num_predict=1, keep_alive="0", timeout=60)
    except OSError:
        pass
    args.out.write_text(cb.record_json(evidence), encoding="utf-8")
    print(f"{evidence['status']}: {evidence.get('reason', '')} ({evidence['wall_seconds']}s, "
          f"{len(evidence['iterations'])} model call(s)) -> {args.out}")
    return 0 if evidence["status"] == cb.SUCCESS and evidence.get("oracle", {}).get("passed", True) else 1


def run_oracle(spec: dict, task: cb.Task, register: dict, oracle: dict) -> dict:
    """Independent verification: the builder's header against a held-out test it never saw, built in a throwaway copy."""
    scratch = Path(spec["build_dir"]).parent / (Path(spec["build_dir"]).name + "-oracle")
    if scratch.exists() and scratch.name.endswith("-oracle"):  # only ever clears a throwaway directory this script made
        shutil.rmtree(scratch)
    shutil.copytree(task.workspace / "src" / "simulation", scratch / "src" / "simulation")
    target = scratch / "src" / "simulation" / register["source"]
    target.write_text(Path(oracle["test_file"]).read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    bld = scratch / "build"
    steps = [["cmake", "-S", str(scratch / "src" / "simulation"), "-B", str(bld), "-G", "Visual Studio 18 2026", "-A", "x64",
              "-DBUILD_TESTING=ON"],
             ["cmake", "--build", str(bld), "--config", "Debug", "--target", register["name"], "--", "/nodeReuse:false", "/v:minimal"],
             ["ctest", "--test-dir", str(bld), "-C", "Debug", "-R", f"^{register['name']}$", "--output-on-failure"]]
    result = {"passed": False, "stage": "", "tail": ""}
    for step in steps:
        code, out = cb.default_run(step, scratch, 900)
        result.update(stage=step[0], tail=out[-700:])
        if code != 0:
            result["diagnostics"] = cb.diagnostics(out, 12)
            return result
    result["passed"] = True
    return result


if __name__ == "__main__":
    raise SystemExit(main())
