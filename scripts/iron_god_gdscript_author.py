"""One release-bound, local Ollama GDScript worker in an isolated Git worktree.

No Claude/API calls, no direct push/merge and no self-authored acceptance test.
The supervisor performs an independent recheck before publication.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for directory in (str(ROOT), str(ROOT / "src")):
    if directory not in sys.path:
        sys.path.insert(0, directory)

from project_orchestrator import iron_god_cpp_builder as bounded  # noqa: E402
from project_orchestrator import iron_god_cpp_lane as common  # noqa: E402
from project_orchestrator import iron_god_gdscript_builder as gdscript  # noqa: E402
from project_orchestrator import iron_god_gdscript_lane as lane  # noqa: E402
from project_orchestrator import iron_god_local_model as local_model  # noqa: E402
from project_orchestrator.state_io import atomic_json  # noqa: E402


def git(folder: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(folder), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=300, check=False)


def run_once(request: dict, clone: Path, base: str, release_repo: Path, release_sha: str) -> dict:
    """Build, verify and commit only one independently specified GDScript unit."""
    result: dict = {"request": request.get("id"), "base": base, "status": "BLOCKED"}
    if not lane.eligible(request, repository=str(request.get("repository") or ""), base=base):
        return {**result, "reason": "UNAUTHORIZED_GODOT_REQUEST"}
    if (not lane.HEX.fullmatch(release_sha)
            or git(release_repo, "rev-parse", "HEAD").stdout.strip() != release_sha):
        return {**result, "reason": "RELEASE_HEAD_MISMATCH"}
    if git(clone, "cat-file", "-e", base + "^{commit}").returncode:
        return {**result, "reason": "EXACT_BASE_MISSING"}
    if git(clone, "rev-parse", "origin/main").stdout.strip() != base:
        return {**result, "reason": "BASE_NOT_CURRENT_ORIGIN_MAIN"}
    # No forced reset or removal of an earlier worktree. Crash leftovers
    # remain inspectable, while a fresh attempt uses a fresh random branch.
    from uuid import uuid4

    key = hashlib.sha256(str(request["id"]).encode()).hexdigest()[:12]
    branch = "ig/godot-" + key + "-" + uuid4().hex[:12]
    workspace = clone.parent / "godot-lane" / branch.replace("/", "-")
    workspace.parent.mkdir(parents=True, exist_ok=True)
    made = git(clone, "worktree", "add", "-b", branch, str(workspace), base)
    if made.returncode:
        return {**result, "reason": "ISOLATED_WORKTREE_FAILED: " + made.stderr[-160:]}
    result.update(branch=branch, workspace=str(workspace))
    try:
        task = gdscript.make_task(request, workspace)
    except (ValueError, OSError) as exc:
        return {**result, "reason": "GODOT_PRECONDITION_FAILED: " + str(exc)[:200]}
    test_path = workspace / request["gdscript"]["test_script"]
    test_digest = hashlib.sha256(test_path.read_bytes()).hexdigest()
    before_status, before_tail = bounded.default_run(task.test, workspace, 120)
    if before_status == 0:
        return {**result, "reason": "BASELINE_TEST_NOT_DISCRIMINATING"}
    if before_status in {124, 127}:
        return {**result, "reason": "BASELINE_GODOT_RUNTIME_UNAVAILABLE: " + before_tail[-150:]}
    cancelled = []

    def ask(prompt: str) -> str:
        try:
            reply = local_model.guarded_generate(
                prompt, requested=None, num_ctx=6144, num_predict=2200,
                timeout=900, wait_s=900,
            )
        except local_model.ModelCancelled as exc:
            cancelled.append(exc)
            raise
        return str(reply.get("response") or "")

    evidence = bounded.build_change(
        task, ask, limits=bounded.Limits(max_iterations=3, memory_waits=0),
        admit=lambda: (True, "the shared guarded Ollama door independently checks memory and cancellation"),
    )
    result["evidence"] = {key: evidence.get(key) for key in
                          ("status", "reason", "iterations", "files", "rolled_back")}
    if cancelled:
        return {**result, "status": "WAITING_FOR_MEMORY", "reason": "CANCELLED_ON_PRESSURE"}
    if evidence.get("status") != bounded.SUCCESS:
        why = evidence.get("reason") or evidence.get("status")
        return {**result, "status": "FAILED", "reason": str(why)[:300]}
    if hashlib.sha256(test_path.read_bytes()).hexdigest() != test_digest:
        return {**result, "status": "FAILED", "reason": "INDEPENDENT_TEST_TAMPERED"}
    diff = git(workspace, "status", "--porcelain", "--untracked-files=all")
    paths = [row[3:].replace("\\", "/") for row in diff.stdout.splitlines() if len(row) >= 4]
    if diff.returncode or paths != [request["gdscript"]["module"]]:
        return {**result, "status": "FAILED", "reason": "UNEXPECTED_WORKTREE_CHANGES"}
    added = git(workspace, "add", "--", request["gdscript"]["module"])
    if added.returncode:
        return {**result, "status": "FAILED", "reason": "GIT_ADD_FAILED"}
    committed = git(workspace, "commit", "-q", "-m", f"iron-god: Godot {request['id']}"[:140])
    if committed.returncode:
        return {**result, "status": "FAILED", "reason": "COMMIT_FAILED"}
    head = git(workspace, "rev-parse", "HEAD").stdout.strip()
    result["head"] = head
    preflight = common.preflight_record(workspace, head)
    result["preflight"] = preflight
    if not preflight.get("ok"):
        return {**result, "status": "FAILED", "reason": "CANONICAL_FULL_PREFLIGHT_NOT_GREEN"}
    return {**result, "status": "BUILT_AND_VERIFIED"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--clone", type=Path, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--release-repo", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        outcome = run_once(request, args.clone, args.base, args.release_repo,
                           os.environ.get("IRON_GOD_ACCEPTED_SHA", ""))
    except (OSError, ValueError, subprocess.SubprocessError, KeyError) as exc:
        outcome = {"request": request.get("id") if isinstance(locals().get("request"), dict) else None,
                   "base": args.base, "status": "BLOCKED",
                   "reason": f"{type(exc).__name__}: {str(exc)[:250]}"}
    atomic_json(args.result, outcome)
    return 0 if outcome.get("status") == "BUILT_AND_VERIFIED" else 3 if outcome.get("status") == "WAITING_FOR_MEMORY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
