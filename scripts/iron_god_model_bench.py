"""Identical-workload benchmark of the local Ollama models on this laptop.

    python scripts/iron_god_model_bench.py --models qwen2.5-coder:3b,qwen2.5-coder:7b --workloads py,cpp,decomp,tools --out C:/ev/bench

Every model runs the same four workloads, one model and one generation at a time, through the engine's own Ollama
client and its single model slot (so it can never overlap an engine run):

* py      three Python functions/classes judged by hidden pytest files
* cpp     two Everward simulation-core headers built by the real C++ lane (cmake + MSVC + ctest) and judged by a
          held-out oracle test the model never sees
* decomp  one roadmap item decomposed into bounded slices (valid JSON, bounded paths, acceptance present)
* tools   structured tool calls scored by exact match

Per model it records completion, compile/test success, tokens/s, latency, load time, peak memory, CPU and how long
memory takes to recover after the model is unloaded. Nothing is installed, deleted or committed.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
for _path in (str(ROOT), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from project_orchestrator import iron_god_cpp_builder as cb  # noqa: E402
from project_orchestrator import iron_god_cpp_lane as lane  # noqa: E402
from project_orchestrator import iron_god_local_model as lm  # noqa: E402

GIB = 1024 ** 3


def _load_author():
    spec = importlib.util.spec_from_file_location("iron_god_cpp_author", ROOT / "scripts" / "iron_god_cpp_author.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Journal:
    """Append-only, fsynced record of everything measured, so a crash loses at most the case in progress.

    Every generation, every finished case and periodic resource readings are written and flushed to disk the moment they
    exist. A rerun reads the journal first and skips cases already finished (``done``), so work is never repeated or lost.
    A torn last line from a crash is ignored.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.rows: list[dict] = []
        if path.is_file():
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    self.rows.append(json.loads(line))
                except ValueError:
                    continue  # a half-written final line
            raw = path.read_bytes()
            if raw and not raw.endswith(bytes([10])):  # a torn last line: start the next row on a fresh line
                with path.open("ab") as handle:
                    handle.write(bytes([10]))
                    handle.flush()
                    os.fsync(handle.fileno())

    def add(self, kind: str, model: str, **fields) -> dict:
        row = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, "model": model, **fields}
        with self.lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + chr(10))
                handle.flush()
                os.fsync(handle.fileno())
            self.rows.append(row)
        return row

    def done(self, model: str, workload: str, case: str) -> dict | None:
        for row in self.rows:
            if row["kind"] == "case" and row["model"] == model and row["workload"] == workload and row["case"] == case:
                if str(row["result"].get("detail", "")).startswith("HARNESS_ERROR"):
                    continue  # a harness fault says nothing about the model: measure it again on resume
                return row["result"]
        return None

    def of(self, kind: str, model: str) -> list[dict]:
        return [r for r in self.rows if r["kind"] == kind and r["model"] == model]


def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


# --- measurement ---------------------------------------------------------------------------------------------------------
class Sampler:
    """Polls system memory, the Ollama processes' memory and CPU while a model works."""

    def __init__(self, journal: "Journal | None" = None, model: str = "") -> None:
        self.journal, self.model = journal, model
        self.stop_flag = threading.Event()
        self.min_available = psutil.virtual_memory().available / GIB
        self.peak_ollama = 0.0
        self.cpu: list[float] = []
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _ollama_rss(self) -> float:
        total = 0
        for proc in psutil.process_iter(["name", "memory_info"]):
            name = (proc.info.get("name") or "").lower()
            if "ollama" in name or "llama-server" in name:
                total += proc.info["memory_info"].rss if proc.info.get("memory_info") else 0
        return total / GIB

    def _run(self) -> None:
        psutil.cpu_percent(interval=None)
        last_saved = time.monotonic()
        while not self.stop_flag.wait(0.5):
            self.min_available = min(self.min_available, psutil.virtual_memory().available / GIB)
            self.peak_ollama = max(self.peak_ollama, self._ollama_rss())
            self.cpu.append(psutil.cpu_percent(interval=None))
            if self.journal is not None and time.monotonic() - last_saved >= 10:
                last_saved = time.monotonic()
                self.journal.add("resource", self.model, min_available_gib=round(self.min_available, 2),
                                 peak_ollama_gib=round(self.peak_ollama, 2),
                                 cpu_mean_pct=round(statistics.mean(self.cpu[-20:]), 1))

    def __enter__(self) -> "Sampler":
        self.thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop_flag.set()
        self.thread.join(5)


class Meter:
    """Generation statistics for one model across all workloads."""

    def __init__(self, client: lm.OllamaClient, model: str, ctx: int, journal: Journal) -> None:
        self.client, self.model, self.ctx, self.journal = client, model, ctx, journal
        self.calls: list[dict] = [{k: v for k, v in r.items() if k not in {"t", "kind", "model"}}
                                  for r in journal.of("generation", model)]  # earlier sessions of a resumed run
        self.errors: list[str] = []

    def generate(self, prompt: str, *, num_predict: int = 1400, json_mode: bool = False, timeout: float = 900.0,
                 **_ignored) -> dict:
        started = time.monotonic()
        try:
            reply = self.client.generate(self.model, prompt, num_ctx=self.ctx, num_predict=num_predict, keep_alive="10m",
                                         timeout=timeout, temperature=0.0, json_mode=json_mode)
        except (OSError, lm.ModelCancelled) as exc:
            self.errors.append(str(exc)[:200])
            self.journal.add("error", self.model, detail=str(exc)[:200])
            return {"response": "", "model_used": self.model, "error": str(exc)[:200]}
        wall = time.monotonic() - started
        count, dur = reply.get("eval_count") or 0, (reply.get("eval_duration") or 0) / 1e9
        self.calls.append({"wall_s": round(wall, 2), "eval_count": count, "tok_s": round(count / dur, 2) if dur else None,
                           "load_s": round((reply.get("load_duration") or 0) / 1e9, 2),
                           "prompt_tokens": reply.get("prompt_eval_count"), "done_reason": reply.get("done_reason")})
        self.journal.add("generation", self.model, **self.calls[-1])
        reply["model_used"] = self.model
        return reply


def record(journal: Journal, meter: "Meter", entry: dict) -> dict:
    """Persist one finished case at once and return it."""
    journal.add("case", meter.model, workload=entry["workload"], case=entry["case"], result=entry)
    return entry


# --- python workload -----------------------------------------------------------------------------------------------------
PY_TASKS = [
    {"id": "py-1-normalize", "level": 1, "module": "textutil", "spec":
        "def normalize_spaces(text: str) -> str: collapse every run of whitespace (spaces, tabs, newlines) to a single space "
        "and strip leading/trailing whitespace.",
     "tests": '''
from textutil import normalize_spaces
def test_basic(): assert normalize_spaces("  a   b ") == "a b"
def test_mixed(): assert normalize_spaces("a\\t\\nb\\n\\nc") == "a b c"
def test_empty(): assert normalize_spaces("   ") == ""
def test_single(): assert normalize_spaces("x") == "x"
'''},
    {"id": "py-2-intervals", "level": 2, "module": "intervals", "spec":
        "def merge_intervals(items: list[tuple[int, int]]) -> list[tuple[int, int]]: merge overlapping or touching "
        "intervals (touching means one ends where the next starts) and return them sorted by start. The input may be "
        "unsorted and may be empty. Do not modify the input list.",
     "tests": '''
from intervals import merge_intervals
def test_empty(): assert merge_intervals([]) == []
def test_overlap(): assert merge_intervals([(1, 3), (2, 6), (8, 10)]) == [(1, 6), (8, 10)]
def test_touching(): assert merge_intervals([(1, 2), (2, 3)]) == [(1, 3)]
def test_unsorted(): assert merge_intervals([(8, 10), (1, 3), (2, 4)]) == [(1, 4), (8, 10)]
def test_nested(): assert merge_intervals([(1, 10), (2, 3)]) == [(1, 10)]
def test_input_untouched():
    data = [(5, 6), (1, 2)]
    merge_intervals(data)
    assert data == [(5, 6), (1, 2)]
'''},
    {"id": "py-3-lru", "level": 3, "module": "lru", "spec":
        "class LRUCache: constructor LRUCache(capacity: int) raises ValueError when capacity < 1; get(key) returns the value "
        "or None and marks the key most recently used; put(key, value) inserts or updates, marks it most recently used and "
        "evicts the least recently used key when the size would exceed capacity; __len__ returns the number of stored keys; "
        "__contains__ does NOT change recency.",
     "tests": '''
import pytest
from lru import LRUCache
def test_capacity_validation():
    with pytest.raises(ValueError): LRUCache(0)
def test_basic():
    c = LRUCache(2); c.put("a", 1); c.put("b", 2)
    assert c.get("a") == 1 and len(c) == 2
def test_evicts_least_recent():
    c = LRUCache(2); c.put("a", 1); c.put("b", 2); c.get("a"); c.put("c", 3)
    assert "b" not in c and "a" in c and "c" in c
def test_update_refreshes():
    c = LRUCache(2); c.put("a", 1); c.put("b", 2); c.put("a", 9); c.put("c", 3)
    assert c.get("a") == 9 and c.get("b") is None
def test_contains_keeps_recency():
    c = LRUCache(2); c.put("a", 1); c.put("b", 2); assert "a" in c; c.put("c", 3)
    assert "a" not in c
def test_missing(): assert LRUCache(1).get("zz") is None
'''},
]
FENCE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.S)


def run_py(meter: Meter, journal: Journal, tmp_root: Path) -> list[dict]:
    results = []
    for task in PY_TASKS:
        prior = journal.done(meter.model, "py", task["id"])
        if prior:
            results.append(prior)
            continue
        prompt = (f"Write the complete Python 3 module {task['module']}.py implementing exactly this:\n{task['spec']}\n\n"
                  "Standard library only. Reply with exactly one ```python code block containing the whole module and nothing else.")
        started = time.monotonic()
        reply = meter.generate(prompt, num_predict=1000)
        text = str(reply.get("response", ""))
        found = FENCE.search(text)
        code = found.group(1) if found else text
        work = Path(tempfile.mkdtemp(prefix="bench-py-", dir=tmp_root))
        (work / f"{task['module']}.py").write_text(code, encoding="utf-8")
        (work / "test_hidden.py").write_text(task["tests"], encoding="utf-8")
        try:
            proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"], cwd=work,
                                  capture_output=True, text=True, timeout=90, check=False)
            ok, detail = proc.returncode == 0, (proc.stdout + proc.stderr)[-300:]
        except subprocess.TimeoutExpired:
            ok, detail = False, "TEST_TIMEOUT"
        results.append(record(journal, meter, {
            "workload": "py", "case": task["id"], "level": task["level"], "ok": ok,
            "detail": "PASS" if ok else detail.strip().splitlines()[-1][:160] if detail.strip() else "EMPTY",
            "seconds": round(time.monotonic() - started, 1), "error": reply.get("error")}))
    return results


# --- structured workloads ------------------------------------------------------------------------------------------------
TOOLS = [
    {"name": "read_file", "args": {"path": "string"}}, {"name": "write_file", "args": {"path": "string", "content": "string"}},
    {"name": "run_tests", "args": {"target": "string"}}, {"name": "git_commit", "args": {"message": "string"}},
    {"name": "search_code", "args": {"pattern": "string", "directory": "string"}},
]
TOOL_CASES = [
    ("Show me what is inside src/app/main.py", "read_file", {"path": "src/app/main.py"}),
    ("Run the tests in tests/test_parser.py", "run_tests", {"target": "tests/test_parser.py"}),
    ("Commit the work with the message 'fix: handle empty input'", "git_commit", {"message": "fix: handle empty input"}),
    ("Find every use of parse_header under the src directory", "search_code", {"pattern": "parse_header", "directory": "src"}),
    ("Create notes.txt containing the text hello world", "write_file", {"path": "notes.txt", "content": "hello world"}),
    ("Open README.md", "read_file", {"path": "README.md"}),
    ("Please run the whole suite called tests", "run_tests", {"target": "tests"}),
    ("Search for TODO in the docs folder", "search_code", {"pattern": "TODO", "directory": "docs"}),
]


def run_tools(meter: Meter, journal: Journal) -> list[dict]:
    spec = json.dumps(TOOLS)
    results = []
    for index, (request, tool, args) in enumerate(TOOL_CASES):
        prior = journal.done(meter.model, "tools", f"tool-{index + 1}")
        if prior:
            results.append(prior)
            continue
        prompt = (f"You can call exactly one tool. Tools (name and argument types): {spec}\n"
                  f"Request: {request}\n"
                  'Reply with only a JSON object: {"tool": "<name>", "args": {<arguments>}}.')
        started = time.monotonic()
        reply = meter.generate(prompt, num_predict=120, json_mode=True, timeout=300)
        try:
            data = json.loads(str(reply.get("response", "")))
            ok = data.get("tool") == tool and {k: str(v).strip() for k, v in (data.get("args") or {}).items()} == args
            detail = "PASS" if ok else f"got {json.dumps(data)[:120]}"
        except (ValueError, AttributeError):
            ok, detail = False, "INVALID_JSON"
        results.append(record(journal, meter, {"workload": "tools", "case": f"tool-{index + 1}", "ok": ok, "detail": detail,
                                               "seconds": round(time.monotonic() - started, 1), "error": reply.get("error")}))
    return results


ROADMAP_ITEM = ("Add a 'report export' capability to a Python package named reportkit: reports can already be built in "
                "src/reportkit/builder.py; users need to export a report to CSV and to JSON from the command line "
                "(src/reportkit/cli.py), with tests under tests/.")


def run_decomp(meter: Meter, journal: Journal) -> list[dict]:
    prior = journal.done(meter.model, "decomp", "decomp-1")
    if prior:
        return [prior]
    prompt = ("Decompose this roadmap item into 3 to 5 small, independently reviewable slices that can each be built in one "
              f"pull request.\nItem: {ROADMAP_ITEM}\n"
              'Reply with only JSON: {"slices": [{"id": "S1", "objective": "...", "acceptance": ["..."], '
              '"allowed_paths": ["relative/path.py"]}]}. Paths must be relative and inside src/reportkit or tests.')
    started = time.monotonic()
    reply = meter.generate(prompt, num_predict=900, json_mode=True, timeout=600)
    problems = []
    try:
        slices = json.loads(str(reply.get("response", ""))).get("slices")
        if not isinstance(slices, list) or not 3 <= len(slices) <= 5:
            problems.append(f"SLICE_COUNT:{len(slices) if isinstance(slices, list) else 'none'}")
        else:
            ids = [str(s.get("id")) for s in slices]
            if len(set(ids)) != len(ids):
                problems.append("DUPLICATE_IDS")
            for s in slices:
                if not str(s.get("objective", "")).strip():
                    problems.append("EMPTY_OBJECTIVE")
                if not isinstance(s.get("acceptance"), list) or not s["acceptance"]:
                    problems.append("NO_ACCEPTANCE")
                paths = s.get("allowed_paths")
                if not isinstance(paths, list) or not paths:
                    problems.append("NO_PATHS")
                else:
                    for path in paths:
                        norm = str(path).replace("\\", "/")
                        if norm.startswith("/") or ".." in norm.split("/") or not (norm.startswith("src/reportkit") or norm.startswith("tests/")):
                            problems.append(f"BAD_PATH:{norm[:40]}")
    except (ValueError, AttributeError):
        problems.append("INVALID_JSON")
    return [record(journal, meter, {"workload": "decomp", "case": "decomp-1", "ok": not problems,
                                    "detail": "PASS" if not problems else ";".join(sorted(set(problems)))[:200],
                                    "seconds": round(time.monotonic() - started, 1), "error": reply.get("error")})]


# --- c++ workload (real lane, real MSVC) ---------------------------------------------------------------------------------
PI = "3.14159265358979323846"
CPP_CASES = [
    {"id": "EW-BENCH-1", "fn": "clamp_unit", "header": "clamp_unit",
     "objective": "Add a header-only helper everward::simulation::clamp_unit(double) that returns its argument limited to the range [0.0, 1.0], and returns 0.0 for NaN.",
     "acceptance": ["clamp_unit(0.5) == 0.5", "clamp_unit(-3.0) == 0.0", "clamp_unit(7.25) == 1.0", "clamp_unit(NaN) == 0.0"],
     "oracle": '''#include "everward/simulation/clamp_unit.hpp"
#undef NDEBUG
#include <cassert>
#include <limits>
using namespace everward::simulation;
int main() {
    assert(clamp_unit(0.0) == 0.0 && clamp_unit(1.0) == 1.0 && clamp_unit(0.25) == 0.25);
    assert(clamp_unit(-0.0001) == 0.0 && clamp_unit(1.0001) == 1.0);
    assert(clamp_unit(-1e300) == 0.0 && clamp_unit(1e300) == 1.0);
    assert(clamp_unit(std::numeric_limits<double>::infinity()) == 1.0);
    assert(clamp_unit(-std::numeric_limits<double>::infinity()) == 0.0);
    assert(clamp_unit(std::numeric_limits<double>::quiet_NaN()) == 0.0);
    return 0;
}
'''},
    {"id": "EW-BENCH-2", "fn": "wrap_angle", "header": "wrap_angle",
     "objective": "Add a header-only helper everward::simulation::wrap_angle(double radians) that returns the equivalent angle in the half-open range (-pi, pi], and returns 0.0 for NaN or infinite input.",
     "acceptance": ["wrap_angle(0.0) == 0.0", "wrap_angle(3*pi) is within 1e-9 of pi", "wrap_angle(-pi) is within 1e-9 of pi",
                    "wrap_angle(-0.5*pi) is within 1e-9 of -0.5*pi", "wrap_angle(NaN) == 0.0"],
     "oracle": f'''#include "everward/simulation/wrap_angle.hpp"
#undef NDEBUG
#include <cassert>
#include <cmath>
#include <limits>
using namespace everward::simulation;
static bool near(double a, double b) {{ return std::fabs(a - b) < 1e-9; }}
int main() {{
    const double pi = {PI};
    assert(wrap_angle(0.0) == 0.0);
    assert(near(wrap_angle(pi), pi) && near(wrap_angle(-pi), pi));
    assert(near(wrap_angle(3 * pi), pi) && near(wrap_angle(2 * pi), 0.0));
    assert(near(wrap_angle(-0.5 * pi), -0.5 * pi) && near(wrap_angle(0.5 * pi), 0.5 * pi));
    assert(near(wrap_angle(7.0), 7.0 - 2 * pi) && near(wrap_angle(-7.0), -7.0 + 2 * pi));
    assert(near(wrap_angle(100 * pi + 0.25), 0.25));
    assert(wrap_angle(std::numeric_limits<double>::quiet_NaN()) == 0.0);
    assert(wrap_angle(std::numeric_limits<double>::infinity()) == 0.0);
    {{ const double w = wrap_angle(-1e6); assert(w > -pi - 1e-9 && w <= pi + 1e-9); }}
    return 0;
}}
'''},
]


def cpp_request(case: dict) -> dict:
    base = "src/simulation"
    header = f"{base}/include/everward/simulation/{case['header']}.hpp"
    test = f"{base}/tests/{case['header']}_tests.cpp"
    return {"id": case["id"], "repository": "jweter/Project-Everward", "roadmap_ref": "model-bench#cpp",
            "objective": case["objective"], "slice_acceptance": case["acceptance"],
            "allowed_paths": [header, test],
            "forbidden_paths": [f"{base}/CMakeLists.txt", ".github/", "engineering/", "docs/", "THIRD_PARTY_NOTICES.md", "LICENSE"],
            "cpp": {"header": header, "test": test, "context": [f"{base}/tests/fix_it_tests.cpp#L1-30"],
                    "rules": ["C++20, header-only: start the header with #pragma once and define the function as inline in namespace everward::simulation.",
                              "Do not edit any other file."],
                    "oracle_test": f"{base}/tests/oracle/{case['header']}_oracle.cpp"}}


def run_cpp(meter: Meter, journal: Journal, clone: Path, base: str, work: Path) -> list[dict]:
    author = _load_author()
    results = []
    for case in CPP_CASES:
        prior = journal.done(meter.model, "cpp", case["id"])
        if prior:
            results.append(prior)
            continue
        request = cpp_request(case)
        profile = lane.classify(request)
        started = time.monotonic()
        branch = "ig/bench-" + re.sub(r"[^a-z0-9]+", "-", f"{meter.model}-{case['id']}".lower())
        entry = {"workload": "cpp", "case": case["id"], "ok": False, "detail": "", "compiled": False, "oracle": None}
        try:
            workspace = author.isolated_worktree(clone, base, branch)
            outcome = lane.author(request, workspace, workspace.parent / (workspace.name + "-build"), generate=meter.generate,
                                  limits=cb.Limits(max_iterations=3))
            iterations = outcome.evidence.get("iterations", [])
            entry["iterations"] = len(iterations)
            entry["compiled"] = outcome.ok
            if outcome.ok:
                oracle = workspace / request["cpp"]["oracle_test"]
                oracle.parent.mkdir(parents=True, exist_ok=True)
                oracle.write_text(case["oracle"], encoding="utf-8")
                check = author.oracle_check(request, workspace, profile)
                entry["oracle"] = check.get("passed")
                entry["ok"] = bool(check.get("passed"))
                entry["detail"] = "PASS" if entry["ok"] else ("ORACLE_FAILED " + str(check.get("tail", ""))[-160:])
            else:
                last = iterations[-1] if iterations else {}
                entry["detail"] = f"{outcome.evidence.get('status', outcome.blocked)}: " + "; ".join(last.get("diagnostics", [])[:2])[:200]
        except Exception as exc:  # noqa: BLE001 - a broken run is a measured failure, never a crash of the benchmark
            entry["detail"] = f"HARNESS_ERROR {type(exc).__name__}: {str(exc)[:160]}"
        finally:
            for suffix in ("", "-build", "-oracle"):
                target = clone.parent / "cpp-lane" / (branch.replace("/", "_") + suffix)
                if target.exists():
                    subprocess.run(["git", "-C", str(clone), "worktree", "remove", "--force", str(target)], capture_output=True, check=False)
                    shutil.rmtree(target, ignore_errors=True)
        entry["seconds"] = round(time.monotonic() - started, 1)
        results.append(record(journal, meter, entry))
    return results


# --- driver --------------------------------------------------------------------------------------------------------------
def admit(model: str, wait_s: float) -> tuple[lm.Gate, str]:
    match = lm._SIZE.search(lm.bare_name(model).lower())
    key = f"{float(match.group(1)):g}b" if match else "7b"
    gib, reserve, _idle = lm.PROFILES.get(key, lm.PROFILES["7b"])
    gate = lm.Gate(model_gib=gib, reserve_gib=reserve, min_idle_s=0.0)  # the owner is supervising: idle is not required here
    deadline = time.monotonic() + wait_s
    while True:
        ok, why = gate.admit()
        if ok:
            return gate, why
        if time.monotonic() >= deadline:
            raise lm.ModelNotAdmitted(why)
        time.sleep(3)


def bench_model(model: str, workloads: list[str], args: argparse.Namespace, tmp: Path, journal: Journal) -> dict:
    client = lm.OllamaClient()
    out: dict = {"model": model, "started_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        gate, why = admit(model, args.wait_s)
    except lm.ModelNotAdmitted as exc:
        return {**out, "status": "NOT_ADMITTED", "reason": str(exc)}
    for stray in [m for m in client.loaded() if m != model]:
        client.unload(stray)
    baseline = psutil.virtual_memory().available / GIB
    journal.add("progress", model, state="STARTED", baseline_available_gib=round(baseline, 2))
    meter = Meter(client, model, args.ctx, journal)
    results: list[dict] = []
    t0 = time.monotonic()
    with Sampler(journal, model) as sampler:
        if "py" in workloads:
            results += run_py(meter, journal, tmp)
        if "tools" in workloads:
            results += run_tools(meter, journal)
        if "decomp" in workloads:
            results += run_decomp(meter, journal)
        if "cpp" in workloads:
            results += run_cpp(meter, journal, Path(args.everward_clone), args.base, tmp)
    elapsed = time.monotonic() - t0
    unloaded = client.unload(model)
    unload_started = time.monotonic()
    recovered_s = None
    while time.monotonic() - unload_started < 90:
        if psutil.virtual_memory().available / GIB >= baseline - 0.5:
            recovered_s = round(time.monotonic() - unload_started, 1)
            break
        time.sleep(1)
    speeds = [c["tok_s"] for c in meter.calls if c["tok_s"]]
    walls = [c["wall_s"] for c in meter.calls]
    out.update({
        "status": "DONE", "admitted": why, "results": results, "elapsed_s": round(elapsed, 1),
        "generations": len(meter.calls), "generation_errors": meter.errors[:5],
        "tok_s_median": round(statistics.median(speeds), 2) if speeds else None,
        "tok_s_min": min(speeds) if speeds else None,
        "latency_median_s": round(statistics.median(walls), 2) if walls else None,
        "latency_max_s": max(walls) if walls else None,
        "load_s_first": next((c["load_s"] for c in meter.calls), None),
        "baseline_available_gib": round(baseline, 2),
        "min_available_gib": round(min([sampler.min_available] + [r["min_available_gib"] for r in journal.of("resource", model)]), 2),
        "peak_ollama_gib": round(max([sampler.peak_ollama] + [r["peak_ollama_gib"] for r in journal.of("resource", model)]), 2),
        "cpu_mean_pct": round(statistics.mean(sampler.cpu + [r["cpu_mean_pct"] for r in journal.of("resource", model)]), 1)
        if (sampler.cpu or journal.of("resource", model)) else None,
        "cpu_peak_pct": max(sampler.cpu) if sampler.cpu else None,
        "unload_confirmed": unloaded, "memory_recovery_s": recovered_s, "gate_lowest_gib": round(gate.lowest, 2)})
    return out


def score(result: dict) -> dict:
    by: dict[str, list[bool]] = {}
    for row in result.get("results", []):
        by.setdefault(row["workload"], []).append(bool(row["ok"]))
    return {k: f"{sum(v)}/{len(v)}" for k, v in by.items()}


def markdown(all_results: list[dict]) -> str:
    lines = ["| model | py | cpp | decomp | tools | tok/s (median) | latency med s | load s | peak ollama GiB | min free GiB | CPU mean % | recovery s |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in all_results:
        if r.get("status") != "DONE":
            lines.append(f"| {r['model']} | {r.get('status')}: {r.get('reason', '')[:60]} |||||||||||")
            continue
        s = score(r)
        lines.append(f"| {r['model']} | {s.get('py', '-')} | {s.get('cpp', '-')} | {s.get('decomp', '-')} | {s.get('tools', '-')} | "
                     f"{r['tok_s_median']} | {r['latency_median_s']} | {r['load_s_first']} | {r['peak_ollama_gib']} | "
                     f"{r['min_available_gib']} | {r['cpu_mean_pct']} | {r['memory_recovery_s']} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", required=True)
    parser.add_argument("--workloads", default="py,tools,decomp,cpp")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ctx", type=int, default=6144)
    parser.add_argument("--wait-s", type=float, default=600.0)
    parser.add_argument("--everward-clone", default="C:/ev/smoke/Project-Everward")
    parser.add_argument("--base", default="")
    args = parser.parse_args(argv)
    workloads = [w for w in args.workloads.split(",") if w]
    if "cpp" in workloads and not args.base:
        parser.error("--base (an Everward commit sha) is required for the cpp workload")
    args.out.mkdir(parents=True, exist_ok=True)
    journal = Journal(args.out / "journal.jsonl")
    if journal.rows:
        print(f"resuming: {sum(1 for r in journal.rows if r['kind'] == 'case')} finished cases already in the journal", flush=True)
    tmp = Path(tempfile.mkdtemp(prefix="ig-bench-"))
    collected: list[dict] = []
    for model in [m for m in args.models.split(",") if m]:
        try:
            with lm.model_slot(args.wait_s):
                result = bench_model(model, workloads, args, tmp, journal)
        except lm.ModelNotAdmitted as exc:
            result = {"model": model, "status": "SLOT_BUSY", "reason": str(exc)}
        collected.append(result)
        write_atomic(args.out / f"{re.sub(r'[^a-z0-9.]+', '_', model.lower())}.json", json.dumps(result, indent=2))
        journal.add("progress", model, state=result.get("status"))
        print(f"{model}: {result.get('status')} {score(result) if result.get('status') == 'DONE' else result.get('reason', '')}", flush=True)
    write_atomic(args.out / "summary.md", markdown(collected))
    write_atomic(args.out / "summary.json", json.dumps(collected, indent=2))
    print(markdown(collected))
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
