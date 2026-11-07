"""Build one roadmap C++ request with the local model, in its own process and an isolated git worktree.

    python scripts/iron_god_cpp_author.py --request request.json --clone <repo clone> --base <sha> --result result.json

Like the other local authors it runs bound to the release (``IRON_GOD_ACCEPTED_SHA``) and goes through the shared guarded
Ollama door, so it uses the single model slot, the 3B model unless a larger one is admitted right now, and is cancelled on
memory pressure. It never pushes: it leaves a verified commit on a local branch plus an evidence file, and the lane's
guarded GitHub path delivers it. Exit codes: 0 built and verified, 3 waiting for memory (no attempt spent), 1 failed/blocked.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _path in (str(ROOT), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from project_orchestrator import iron_god_cpp_builder as cb  # noqa: E402
from project_orchestrator import iron_god_cpp_lane as lane  # noqa: E402
from project_orchestrator import iron_god_local_model as lm  # noqa: E402


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=600, check=False)


def isolated_worktree(clone: Path, base: str, branch: str) -> Path:
    """A fresh detached-from-main worktree for this request only; any earlier leftover of the same name is removed first."""
    path = clone.parent / "cpp-lane" / branch.replace("/", "_")
    if path.exists():
        git(clone, "worktree", "remove", "--force", str(path))
        shutil.rmtree(path, ignore_errors=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    made = git(clone, "worktree", "add", "--quiet", "-B", branch, str(path), base)
    if made.returncode != 0:
        raise RuntimeError("WORKTREE_FAILED: " + (made.stderr or "")[-200:])
    return path


def oracle_check(request: dict, workspace: Path, profile: lane.CppProfile) -> dict:
    """Optional owner-approved held-out test: built against the new header in a throwaway copy the model never saw."""
    oracle = (request.get("cpp") or {}).get("oracle_test")
    if not oracle:
        return {"checked": False, "passed": True}
    source = workspace / oracle
    if not source.is_file():
        return {"checked": True, "passed": False, "tail": "ORACLE_TEST_MISSING"}
    scratch = workspace.parent / (workspace.name + "-oracle")
    shutil.rmtree(scratch, ignore_errors=True)
    shutil.copytree(workspace / Path(profile.cmake_file).parent, scratch / Path(profile.cmake_file).parent)
    name = lane.target_name(str(request["id"]) + "-oracle")
    cmake_dir = scratch / Path(profile.cmake_file).parent
    rel = Path(oracle).relative_to(Path(profile.cmake_file).parent).as_posix()
    lane.register_cmake(scratch, profile, name, rel)
    build = scratch / "build"
    steps = [[part.format(source=str(cmake_dir), build=str(build), generator=profile.generator) for part in profile.configure],
             ["cmake", "--build", str(build), "--config", "Debug", "--target", name, "--", *profile.build_tool_args],
             ["ctest", "--test-dir", str(build), "-C", "Debug", "-R", f"^{name}$", "--output-on-failure"]]
    for step in steps:
        code, out = cb.default_run(step, scratch, 900)
        if code != 0:
            return {"checked": True, "passed": False, "stage": step[0], "tail": out[-700:]}
    return {"checked": True, "passed": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--clone", type=Path, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--wait-s", type=float, default=900.0)
    args = parser.parse_args(argv)
    request = json.loads(args.request.read_text(encoding="utf-8"))
    profile = lane.classify(request)
    out: dict = {"request": request.get("id"), "base": args.base}

    def finish(code: int, **fields) -> int:
        out.update(fields)
        args.result.parent.mkdir(parents=True, exist_ok=True)
        args.result.write_text(cb.record_json(out), encoding="utf-8")
        print(f"{out.get('status')}: {out.get('reason', '')}")
        return code

    if profile is None:
        return finish(1, status="BLOCKED", reason="NOT_A_CPP_REQUEST")
    branch = "ig/cpp-" + lane.target_name(str(request["id"])).removeprefix("ew_").removesuffix("_tests")[:40]
    try:
        workspace = isolated_worktree(args.clone, args.base, branch)
    except RuntimeError as exc:
        return finish(1, status="BLOCKED", reason=str(exc))

    def generate(prompt: str, **kw):
        return lm.guarded_generate(prompt, requested=kw.get("requested"), num_ctx=kw.get("num_ctx", 6144),
                                   num_predict=kw.get("num_predict", 2200), timeout=kw.get("timeout", 900),
                                   wait_s=args.wait_s)

    try:
        outcome = lane.author(request, workspace, workspace.parent / (workspace.name + "-build"), generate=generate)
    except lm.ModelNotAdmitted as exc:  # memory/slot not available: wait, spend nothing
        return finish(3, status="WAITING_FOR_MEMORY", reason=str(exc)[:200])
    except OSError as exc:  # the model runtime itself is broken: a real failure, not an empty answer and not a spent attempt
        return finish(3, status="MODEL_RUNTIME_ERROR", reason=str(exc)[:300])
    except lm.ModelCancelled as exc:
        return finish(3, status="WAITING_FOR_MEMORY", reason="CANCELLED_ON_PRESSURE: " + str(exc)[:160])
    if not outcome.ok:
        out["evidence"] = outcome.evidence
        return finish(1, status="BLOCKED" if outcome.blocked else outcome.evidence.get("status", "FAILED"),
                      reason=outcome.blocked or outcome.evidence.get("reason", ""))
    oracle = oracle_check(request, workspace, profile)
    out["oracle"] = oracle
    out["evidence"] = outcome.evidence
    if not oracle["passed"]:
        return finish(1, status="ORACLE_FAILED", reason=str(oracle.get("tail", ""))[-300:])
    cpp = request["cpp"]
    git(workspace, "add", "--", cpp["header"], cpp["test"], profile.cmake_file)
    committed = git(workspace, "commit", "-q", "-m", f"feat: {request.get('objective', request['id'])}"[:200])
    if committed.returncode != 0:
        return finish(1, status="COMMIT_FAILED", reason=(committed.stderr or committed.stdout)[-200:])
    head = git(workspace, "rev-parse", "HEAD").stdout.strip()
    return finish(0, status="BUILT_AND_VERIFIED", branch=branch, head=head, base=args.base, workspace=str(workspace))


if __name__ == "__main__":
    raise SystemExit(main())
