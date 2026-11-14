"""Run one capable build as an engine stage (owner decision 2026-11-14; see iron_god_capable_engine).

Launched by the product lane like a product worker: its own process, bound to the release identity and to the owner's
execution authority. It makes a worktree at the exact baseline, lets the Claude Code CLI edit files there (file tools
only), commits the candidate as one commit on the baseline, verifies that exact head with the repository's canonical
preflight, feeds a failure back, and - only when the preflight passes - pushes that same head and opens the pull request
itself. It always writes a result file; the lane reads only that.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
for _path in (str(ROOT / "src"), str(ROOT)):
    if _path in sys.path:
        sys.path.remove(_path)
    sys.path.insert(0, _path)

from project_orchestrator import iron_god_capable_builder as capable  # noqa: E402
from project_orchestrator import iron_god_capable_engine as engine  # noqa: E402
from project_orchestrator.execution_authority import evaluate_execution_authority  # noqa: E402
from project_orchestrator.execution_runtime import NativeWindowsRuntime, RuntimeRequest  # noqa: E402
from project_orchestrator.state_io import atomic_json  # noqa: E402

# The owner chose the Claude Code LOGIN on this laptop. A key in the environment would silently bill the API instead.
API_CREDENTIAL_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
IDENTITY = ("-c", "user.name=Iron God Capable Builder", "-c", "user.email=iron-god-capable@localhost")


def _git(cwd: Path, *args: str, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False, timeout=timeout)


def changed_paths(worktree: Path, base: str) -> list[str]:
    """Every path that differs from the baseline - in the candidate commit or not yet committed - as forward-slash paths."""
    committed = _git(worktree, "diff", "--name-only", "--no-renames", "-z", base, "HEAD").stdout
    out = _git(worktree, "status", "--porcelain", "--untracked-files=all", "-z").stdout
    paths, entries, index = [p.replace("\\", "/") for p in committed.split("\0") if p], out.split("\0"), 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) < 4:
            continue
        paths.append(entry[3:].replace("\\", "/"))
        if entry[0] in "RC":  # a rename or copy is followed by its source path: that path changed too
            if index < len(entries) and entries[index]:
                paths.append(entries[index].replace("\\", "/"))
            index += 1
    return sorted(set(paths))


def parse_answer(stdout: str, stderr: str, returncode: int | None) -> dict[str, Any]:
    """The CLI prints one JSON object; anything else is a failed call, reported with the end of what it did print."""
    try:
        answer = json.loads(stdout.strip().splitlines()[-1]) if stdout.strip() else None
    except ValueError:
        answer = None
    if not isinstance(answer, dict):
        return {"is_error": True, "result": f"exit {returncode}: {((stdout or '') + (stderr or ''))[-400:]}"}
    return answer


def pull_request_body(request: dict[str, Any], verdict: dict[str, Any]) -> str:
    rounds = verdict.get("rounds", [])
    return "\n".join([
        f"Roadmap slice `{request['id']}` ({request.get('roadmap_ref', '')}).", "",
        f"**Objective:** {request.get('objective', '')}", "",
        "Built by the capable builder running as an Iron God engine stage: the model edited files only; the engine checked "
        "the changed paths against the slice's fences, ran the repository's canonical preflight and opened this pull "
        "request.", "",
        f"- Rounds: {len(rounds)} (" + ", ".join(
            "scope" if r.get("scope_problems") else ("pass" if r.get("preflight_ok") else "preflight failed")
            for r in rounds) + ")",
        f"- Canonical preflight at this exact head (`{verdict.get('head', '')}`): passed"
        + (" (matched the base commit: only failures already present at the base)"
           if (verdict.get("preflight") or {}).get("baseline_match") else ""),
        "- Changed paths: " + ", ".join(f"`{p}`" for p in verdict.get("changed", [])),
    ])


def title_of(request: dict[str, Any]) -> str:
    return f"Iron God [{request['id']}] {request.get('slice', 'roadmap slice')}"[:200]


def commit_candidate(worktree: Path, base: str, title: str) -> str:
    """Make everything that differs from the baseline ONE commit on the baseline; return its sha.

    One commit, so the preflight's baseline comparison (which reads ``HEAD^``) compares against the baseline in every
    round, and a clean tree, which that comparison requires. The scope check has already passed on every changed path.
    """
    _git(worktree, "reset", "-q", "--soft", base)
    _git(worktree, "add", "-A")
    commit = _git(worktree, *IDENTITY, "commit", "-q", "-m", title)
    if commit.returncode != 0:
        raise RuntimeError("COMMIT_FAILED: " + (commit.stderr or commit.stdout)[-300:])
    return _git(worktree, "rev-parse", "HEAD").stdout.strip()


def restore_head(worktree: Path) -> None:
    """Put the tree back to exactly the committed candidate: whatever a test run left behind is never delivered."""
    _git(worktree, "reset", "-q", "--hard", "HEAD")
    _git(worktree, "clean", "-fdq")


def deliver(job: dict[str, Any], worktree: Path, verdict: dict[str, Any], check: Callable[[], None]) -> str:
    request = job["request"]
    title = title_of(request)
    head = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    if head != verdict.get("head") or _git(worktree, "status", "--porcelain").stdout.strip():
        raise RuntimeError("DELIVERY_REFUSED: the tree is not exactly the head the preflight verified")
    check()
    pushed = _git(worktree, "push", "origin", f"{head}:refs/heads/{job['branch']}", timeout=1800)
    if pushed.returncode != 0:
        raise RuntimeError("PUSH_FAILED: " + pushed.stderr[-300:])
    made = subprocess.run(["gh", "pr", "create", "--repo", request["repository"], "--base", "main", "--head",
                           job["branch"], "--title", title, "--body", pull_request_body(request, verdict)],
                          capture_output=True, text=True, check=False, timeout=300)
    if made.returncode != 0:
        raise RuntimeError("PR_CREATE_FAILED: " + made.stderr[-300:])
    return made.stdout.strip().splitlines()[-1]


def build(job: dict[str, Any], *, check: Callable[[], None], preflight: Callable[[Path], dict[str, Any]],
          executable: str | None) -> dict[str, Any]:
    request, clone, worktree = job["request"], Path(job["clone"]), Path(job["worktree"])
    if not executable:
        return {"ok": False, "reason": "CLAUDE_CLI_NOT_FOUND"}
    added = _git(clone, "worktree", "add", "--quiet", "-B", job["branch"], str(worktree), job["base"])
    if added.returncode != 0:
        return {"ok": False, "reason": "WORKTREE_FAILED: " + added.stderr[-300:]}
    try:
        def ask(prompt: str, session: str | None) -> dict[str, Any]:
            done = NativeWindowsRuntime().execute_supervised(
                RuntimeRequest(engine.claude_argv(executable, prompt, resume=session, model=job.get("model")), worktree,
                               None, engine.ROUND_TIMEOUT_S), check, interval=2.0)
            return parse_answer(done.stdout or "", done.stderr or "", done.returncode)

        def verify() -> dict[str, Any]:
            try:
                return preflight(worktree)
            finally:
                restore_head(worktree)

        verdict = engine.run_rounds(
            request, job["prompt"], str(request.get("graph_path") or capable.GRAPH_PATH), ask=ask,
            changed_paths=lambda: changed_paths(worktree, job["base"]),
            commit=lambda: commit_candidate(worktree, job["base"], title_of(request)), verify=verify, check=check)
        if verdict["ok"]:
            verdict["pr_url"] = deliver(job, worktree, verdict, check)
        return verdict
    finally:
        _git(clone, "worktree", "remove", "--force", str(worktree))
        _git(clone, "worktree", "prune")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build one capable roadmap slice inside the engine.")
    parser.add_argument("--job", required=True, type=Path)
    args = parser.parse_args(argv)
    job = json.loads(args.job.read_text(encoding="utf-8"))
    for name in API_CREDENTIAL_ENV:
        os.environ.pop(name, None)
    release = os.environ.get("IRON_GOD_ACCEPTED_SHA")

    def authority() -> bool:
        try:
            return evaluate_execution_authority(release_sha=release).permitted is True
        except Exception:
            return False

    def check() -> None:
        if not authority():
            raise RuntimeError(engine.INTERRUPTED_MARK)

    spec = importlib.util.spec_from_file_location("iron_god_product_worker", ROOT / "scripts" / "iron_god_product_worker.py")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    try:
        result = build(job, check=check, executable=capable.cli_path(),
                       preflight=lambda tree: worker.production_preflight(tree, check, engine.PREFLIGHT_TIMEOUT_S))
    except Exception as exc:  # a revoked authority, a failed push, a crashed tool: all recorded, none a pass
        result = {"ok": False, "reason": f"{type(exc).__name__}: {str(exc)[:260]}"}
    atomic_json(Path(job["result"]), result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
