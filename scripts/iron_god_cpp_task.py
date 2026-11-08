"""Run one bounded C++ task through the local builder: a local Ollama model, the real compiler and the repository's tests.

No external AI service is called. The model is only the author; scope, compilation, tests, retry limits, admission,
rollback and the optional independent oracle check are deterministic code (``project_orchestrator.iron_god_cpp_builder``).

    python scripts/iron_god_cpp_task.py --task task.json --out evidence.json [--model qwen2.5-coder:3b] [--wait-hours 8]

Safety on a shared desktop (``project_orchestrator.iron_god_local_model.Gate``): a model starts only when the machine has
enough *available* memory for it plus a reserve and, for a model too big to run beside the owner's apps (the 7B), only
after the user has been idle; while it runs, sustained memory pressure or returning user input cancels the in-flight
generation (the connection is closed so the server stops computing) and the model is unloaded. Cancelling is never skipped.
If the conditions never hold within ``--wait-hours``, the task is reported BLOCKED, not forced.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
for _path in (str(_root), str(_root / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from project_orchestrator import iron_god_cpp_builder as cb  # noqa: E402
from project_orchestrator import iron_god_local_model as lm  # noqa: E402

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
    parser.add_argument("--model", default="qwen2.5-coder:3b")
    parser.add_argument("--wait-hours", type=float, default=0.0, help="poll for memory admission this long")
    parser.add_argument("--iterations", type=int, default=4)
    parser.add_argument("--num-ctx", type=int, default=6144)
    parser.add_argument("--num-predict", type=int, default=2200)
    parser.add_argument("--model-timeout", type=int, default=900)
    args = parser.parse_args(argv)
    spec = json.loads(args.task.read_text(encoding="utf-8"))
    workspace, build_dir = Path(spec["workspace"]), Path(spec["build_dir"])
    gate = lm.gate_for(args.model)  # the shared profiles: an unknown model gets the strictest one
    size, reserve = gate.model_gib, gate.reserve_gib
    client = lm.OllamaClient()
    evidence: dict = {"model": args.model, "gate": {"model_gib": size, "reserve_gib": reserve,
                                                      "required_available_gib": gate.required_gib}}

    deadline = time.monotonic() + args.wait_hours * 3600
    last_note = 0.0
    while True:  # scheduling: do not start until the machine can take the model safely
        admitted, why = gate.admit()
        if admitted:
            break
        if time.monotonic() >= deadline:
            evidence.update(status="BLOCKED_NOT_ADMITTED", reason=why, lowest_available_gib=round(gate.lowest, 2))
            args.out.write_text(cb.record_json(evidence), encoding="utf-8")
            print(f"BLOCKED_NOT_ADMITTED: {why}")
            return 3
        if time.monotonic() - last_note > 600:
            print(f"{time.strftime('%H:%M:%S')} waiting: {why}", flush=True)
            last_note = time.monotonic()
        time.sleep(60)

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
        try:
            reply = client.generate(args.model, prompt, num_ctx=args.num_ctx, num_predict=args.num_predict, keep_alive="3m",
                                    timeout=args.model_timeout, should_abort=gate.abort_reason, poll_s=1.0)
        except lm.ModelCancelled:
            evidence["unloaded_after_cancel"] = client.unload(args.model)  # the generation is already cancelled; free the memory
            raise
        stats.append({k: reply.get(k) for k in ("eval_count", "eval_duration", "prompt_eval_count", "prompt_eval_duration",
                                                 "load_duration", "total_duration")})
        return str(reply.get("response", ""))

    limits = cb.Limits(max_iterations=args.iterations, memory_waits=15, memory_wait_s=20.0)
    started = time.time()
    result = cb.build_change(task, ask, limits=limits, admit=gate.admit)
    evidence.update(result)
    evidence["model_calls"] = stats
    evidence["tokens_per_second"] = [round(s["eval_count"] / (s["eval_duration"] / 1e9), 2) for s in stats
                                     if s.get("eval_count") and s.get("eval_duration")]
    evidence["lowest_available_gib"] = round(gate.lowest, 2)
    evidence["wall_seconds"] = round(time.time() - started, 1)
    oracle = spec.get("oracle")
    if oracle and evidence["status"] == cb.SUCCESS:
        evidence["oracle"] = run_oracle(spec, task, register, oracle)
    evidence["model_unloaded"] = client.unload(args.model)  # always hand the model's memory back
    args.out.write_text(cb.record_json(evidence), encoding="utf-8")
    print(f"{evidence['status']}: {evidence.get('reason', '')} ({evidence['wall_seconds']}s, "
          f"{len(evidence['iterations'])} model call(s), lowest available {evidence['lowest_available_gib']} GiB) -> {args.out}")
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
