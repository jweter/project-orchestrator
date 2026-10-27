"""Owner-held Level 2 calibration: can the local model repair EXISTING code?

Level 2 (existing-module changes) stays disabled for autonomous turns until this
measures it reliable. Cases are real history, never invented:

* ``fix`` cases: a merged bug-fix commit on a product repository's main that
  changed one package module (<= MAX_SOURCE_LINES lines) and its tests. The
  module is put back to its pre-fix version while the fix's tests stay; a case
  is kept only if those tests fail on the pre-fix module and pass on the fix
  (verified per case before any model call).

Each attempt gets a fresh worktree, the failing test output (bounded), and
may edit only the module. The tests are restored byte for byte afterwards and
any tampering or edit outside the module is recorded as a failure, never
forgiven. One bounded repair round gets the new failure output. Nothing is
committed, pushed or published.

    python scripts/iron_god_level2_calibration.py mine            # build verified cases (no model)
    python scripts/iron_god_level2_calibration.py run [--limit N] [--edit-format whole|diff]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _path in (str(ROOT / "src"), str(ROOT)):
    if _path in sys.path:
        sys.path.remove(_path)
    sys.path.insert(0, _path)

from project_orchestrator import iron_god_owner_control as owner_controller  # noqa: E402
from project_orchestrator.iron_god_calibration import remove_tree  # noqa: E402
from project_orchestrator.iron_god_canon_lane import WORKER_LEASE_RUNTIME, default_lease_pool  # noqa: E402
from project_orchestrator.state_io import atomic_json  # noqa: E402

REPOSITORIES = ["knowledge-engine-core", "knowledge-engine-ai", "rocksmith-cdlc-generator"]
MAX_SOURCE_LINES = 250  # measured: prompts far beyond a few thousand tokens never finish on this CPU
MAX_FIX_LINES = 30
LEASE_ID = "iron-god-level2-calibration"
TEST_TIMEOUT = 600


def load_worker():
    spec = importlib.util.spec_from_file_location("iron_god_product_worker", ROOT / "scripts" / "iron_god_product_worker.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(cwd: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=check,
                          encoding="utf-8", errors="replace", timeout=600)


def is_test(path: str) -> bool:
    return path.endswith(".py") and any(part in {"tests", "test"} for part in path.split("/"))


def candidate_fixes(clone: Path, limit: int = 1500) -> list[dict[str, Any]]:
    """Small single-module bug fixes that changed tests too, newest first."""
    log = git(clone, "log", "origin/main", "--no-merges", "-i", "--grep=fix", f"-n{limit}", "--format=%H%x09%s")
    found = []
    for line in log.stdout.splitlines():
        sha, _, subject = line.partition("\t")
        files = git(clone, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).stdout.split()
        sources = [f for f in files if f.endswith(".py") and not is_test(f)]
        tests = [f for f in files if is_test(f)]
        others = [f for f in files if not f.endswith((".py", ".md"))]
        if len(sources) != 1 or not tests or others:
            continue
        numstat = git(clone, "diff", "--numstat", f"{sha}^", sha, "--", sources[0]).stdout.split()
        changed = int(numstat[0]) + int(numstat[1]) if len(numstat) >= 2 and numstat[0].isdigit() else 999
        before = git(clone, "show", f"{sha}^:{sources[0]}")
        if before.returncode != 0 or changed > MAX_FIX_LINES or before.stdout.count("\n") > MAX_SOURCE_LINES:
            continue
        found.append({"commit": sha, "subject": subject[:120], "source": sources[0], "tests": tests,
                      "fix_lines": changed, "source_lines": before.stdout.count("\n")})
    return found


def main_environment(worker, clone: Path, check) -> dict[str, Any]:
    """The repository's shared environment, built only from current main.

    Measured: letting a historical commit's lockfile drive the shared environment
    reinstalled it from an old lock (and failed on a Linux-only pin); calibration
    must never disturb the environment production turns use.
    """
    worktree = fresh_worktree(clone, "origin/main", "env-main")
    try:
        return worker.repository_environment(worktree, check)
    finally:
        drop_worktree(clone, worktree)


def run_tests(environment: dict[str, Any], worktree: Path, tests: list[str]) -> dict[str, Any]:
    temp = worktree.parent / f"{worktree.name}.l2.tmp"
    from scripts.iron_god_canon_worker import private_temp_env

    env = private_temp_env({**os.environ, **environment["overrides"], "PYTHONDONTWRITEBYTECODE": "1",
                                   "PYTHONPATH": os.pathsep.join([str(worktree / "src"), str(worktree)])}, temp)
    try:
        proc = subprocess.run([environment["python"], "-m", "pytest", "-q", "-p", "no:cacheprovider", "-x", *tests],
                              cwd=worktree, env=env, capture_output=True, text=True, check=False,
                              timeout=TEST_TIMEOUT, encoding="utf-8", errors="replace")
        return {"ok": proc.returncode == 0, "returncode": proc.returncode, "tail": (proc.stdout + proc.stderr)[-3000:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": None, "tail": "TIMEOUT"}
    finally:
        remove_tree(temp)


def fresh_worktree(clone: Path, sha: str, name: str) -> Path:
    path = clone.parent / "worktrees" / "level2" / name
    if path.exists():
        git(clone, "worktree", "remove", "--force", str(path))
        remove_tree(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    git(clone, "worktree", "add", "--detach", str(path), sha, check=True)
    return path


def drop_worktree(clone: Path, path: Path) -> None:
    git(clone, "worktree", "remove", "--force", str(path))
    remove_tree(path)


def put_back_pre_fix(worktree: Path, case: dict[str, Any]) -> None:
    git(worktree, "checkout", f"{case['commit']}^", "--", case["source"], check=True)


def mine(state_root: Path, check) -> dict[str, Any]:
    worker = load_worker()
    products = state_root.parent / "canon-workspaces" / "products"
    cases, rejected = [], []
    for repo in REPOSITORIES:
        clone = products / repo
        git(clone, "fetch", "-q", "origin", "main")
        environment = main_environment(worker, clone, check)
        for fix in candidate_fixes(clone):
            check()
            case = {"id": f"{repo}:{fix['commit'][:10]}", "repository": repo, "kind": "fix", **fix}
            worktree = fresh_worktree(clone, fix["commit"], "mine")
            try:
                fixed = run_tests(environment, worktree, fix["tests"])
                put_back_pre_fix(worktree, case)
                broken = run_tests(environment, worktree, fix["tests"])
            finally:
                drop_worktree(clone, worktree)
            if fixed["ok"] and not broken["ok"]:
                case["failure"] = broken["tail"][-1500:]
                cases.append(case)
            else:
                rejected.append({"id": case["id"], "fixed_ok": fixed["ok"], "pre_fix_ok": broken["ok"]})
    out = {"schema_version": 1, "mined_at_utc": datetime.now(UTC).isoformat(), "cases": cases, "rejected": rejected}
    atomic_json(state_root / "iron-god-level2" / "cases.json", out)
    return {"cases": len(cases), "rejected": len(rejected),
            "by_repository": {r: sum(1 for c in cases if c["repository"] == r) for r in REPOSITORIES}}


def failure_lines(tail: str, limit: int = 40) -> str:
    keep = [line for line in tail.splitlines() if line.startswith(("E ", ">", "FAILED", "ERROR")) or "Error" in line]
    return "\n".join(keep[:limit]) or tail[-1500:]


def repair_message(case: dict[str, Any], failure: str) -> str:
    return (f"The tests {', '.join(case['tests'])} fail against {case['source']}. The tests are correct and must "
            f"not change. Diagnose the defect and fix only {case['source']} so the tests pass, changing as "
            f"little existing behaviour as possible.\n\nFailing test output:\n{failure_lines(failure)}")


def attempt(worker, environment: dict[str, Any], clone: Path, case: dict[str, Any], edit_format: str,
            check) -> dict[str, Any]:
    from scripts.autonomous_slice_builder import MAX_AIDER_SECONDS, _acquire_ollama_lock, aider_invocation
    from project_orchestrator.execution_runtime import NativeWindowsRuntime, RuntimeRequest

    worktree = fresh_worktree(clone, case["commit"], case["id"].replace(":", "-"))
    result: dict[str, Any] = {"id": case["id"], "edit_format": edit_format}
    started = time.monotonic()
    try:
        put_back_pre_fix(worktree, case)
        originals = {t: (worktree / t).read_bytes() for t in case["tests"]}
        failure = case["failure"]
        for round_name in ("first", "repair"):
            argv, env = worker.lean_aider_invocation(aider_invocation, repair_message(case, failure), [case["source"]])
            argv = [*argv[:-1], "--edit-format", edit_format, argv[-1]]  # the source file stays last
            lock = _acquire_ollama_lock()
            try:
                run = NativeWindowsRuntime().execute_supervised(
                    RuntimeRequest(tuple(argv), worktree, env, MAX_AIDER_SECONDS), check, interval=2.0)
            finally:
                import shutil
                shutil.rmtree(lock, ignore_errors=True)
            for artifact in worktree.glob(".aider*"):
                remove_tree(artifact) if artifact.is_dir() else artifact.unlink()
            tampered = [t for t, data in originals.items() if (worktree / t).read_bytes() != data]
            for t, data in originals.items():
                (worktree / t).write_bytes(data)  # the spec is restored whatever the model did
            changed = [p for p in git(worktree, "diff", "--name-only", "HEAD").stdout.split() if p != case["source"]]
            untracked = git(worktree, "ls-files", "--others", "--exclude-standard").stdout.split()
            tests = run_tests(environment, worktree, case["tests"])
            result[round_name] = {"aider_exit": run.returncode, "tests_ok": tests["ok"], "tampered_tests": tampered,
                                  "changed_outside_module": changed + untracked,
                                  "module_changed": bool(git(worktree, "diff", "--name-only", f"{case['commit']}^",
                                                             "--", case["source"]).stdout.strip())}
            if tests["ok"] or round_name == "repair":
                break
            failure = tests["tail"]
        final = result.get("repair") or result["first"]
        result["passed"] = bool(final["tests_ok"] and not final["changed_outside_module"]
                                and not any(result[r]["tampered_tests"] for r in ("first", "repair") if r in result))
        result["passed_first_try"] = bool(result["first"]["tests_ok"] and not result["first"]["tampered_tests"]
                                          and not result["first"]["changed_outside_module"])
        result["diff"] = git(worktree, "diff", f"{case['commit']}^", "--", case["source"]).stdout[:6000]
    except Exception as exc:  # one failed case never stops the calibration
        result["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        result["passed"] = result["passed_first_try"] = False
    finally:
        result["wall_s"] = round(time.monotonic() - started, 1)
        drop_worktree(clone, worktree)
    return result


def run(state_root: Path, check, *, limit: int | None, edit_format: str) -> dict[str, Any]:
    worker = load_worker()
    cases = json.loads((state_root / "iron-god-level2" / "cases.json").read_text(encoding="utf-8"))["cases"]
    # interleave repositories so a short run still samples every one
    by_repo = [[c for c in cases if c["repository"] == r] for r in REPOSITORIES]
    ordered = [c for group in zip(*by_repo) for c in group] + [c for g in by_repo for c in g[min(map(len, by_repo)):]]
    selected = ordered[:limit] if limit else ordered
    products = state_root.parent / "canon-workspaces" / "products"
    out_path = state_root / "iron-god-level2" / f"results-{edit_format}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    results = []
    environments = {repo: main_environment(worker, products / repo, check)
                    for repo in sorted({c["repository"] for c in selected})}
    for case in selected:
        check()
        results.append(attempt(worker, environments[case["repository"]], products / case["repository"], case,
                               edit_format, check))
        atomic_json(out_path, {"edit_format": edit_format, "results": results})  # progress survives interruption
    summary = {"edit_format": edit_format, "cases": len(results),
               "passed": sum(r["passed"] for r in results),
               "passed_first_try": sum(r["passed_first_try"] for r in results),
               "tampered": sum(1 for r in results for k in ("first", "repair") if r.get(k, {}).get("tampered_tests")),
               "edited_outside_module": sum(1 for r in results if (r.get("repair") or r.get("first") or {}).get(
                   "changed_outside_module")),
               "errors": sum(1 for r in results if r.get("error")),
               "median_wall_s": sorted(r["wall_s"] for r in results)[len(results) // 2] if results else None,
               "results_file": str(out_path)}
    atomic_json(out_path, {"summary": summary, "results": results})
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Owner-held Level 2 calibration (never publishes)")
    parser.add_argument("command", choices=["mine", "run"])
    parser.add_argument("--state-root", type=Path,
                        default=Path(os.environ.get("LOCALAPPDATA", "")) / "ExponentialEngine" / "flow-state")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--edit-format", choices=["whole", "diff"], default="whole")
    args = parser.parse_args(argv)
    root = owner_controller.control_root(args.state_root)

    def check() -> None:
        if owner_controller.owner_status(root) != owner_controller.OWNER_CONTROL_ACTIVE:
            raise RuntimeError("EXECUTION_AUTHORITY_CHANGED: the owner no longer holds control")

    try:
        check()
    except RuntimeError:
        print(json.dumps({"status": "REFUSED", "reason": "OWNER_MUST_HOLD_CONTROL"}))
        return 3
    if args.command == "mine":
        print(json.dumps(mine(args.state_root, check), indent=2))
        return 0
    pool = default_lease_pool()
    try:
        lease = pool.acquire(WORKER_LEASE_RUNTIME, LEASE_ID, owner_pid=os.getpid())
    except Exception as exc:
        print(json.dumps({"status": "REFUSED", "reason": f"MODEL_LEASE_BUSY: {type(exc).__name__}"}))
        return 3
    try:
        print(json.dumps(run(args.state_root, check, limit=args.limit, edit_format=args.edit_format), indent=2))
    finally:
        pool.release(LEASE_ID, lease.lease_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
