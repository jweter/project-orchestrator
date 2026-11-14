"""The capable builder as a build stage inside the engine (owner decision 2026-11-14).

Until now a ``CAPABLE`` request was fired at a cloud routine: the engine sent a prompt, waited for a pull request and
never showed the builder its own test results. The owner asked for the capable builder to be part of the engine, "not as
a separate consult", using the Claude Code login on this laptop.

This module is that transport. Nothing else about the capable builder changes: the same dispatch ledger
(``capable-dispatches``), the same daily budget, the same attempt cap and the same completion signal (a pull request whose
title names the request). What changes is who does the work between dispatch and pull request:

* the lane spawns one release-bound runner process, holding the worker runtime lease, so there is still a single writer;
* the runner makes a worktree at the exact baseline and runs the Claude Code CLI headless with FILE TOOLS ONLY (no shell,
  no MCP servers, never ``bypassPermissions``): the model edits files and nothing else;
* the ENGINE checks the changed paths against the slice's fences and runs the repository's canonical preflight - the same
  verification every local slice gets - and feeds a failure back to the same session, at most ``MAX_ROUNDS`` rounds;
* the ENGINE commits, pushes and opens the pull request. The model never touches git, GitHub or the network.

An owner takeover kills the runner (it runs under execution authority) and spends no attempt, like every other stage.
The logic here is deterministic and takes its effects as callables; ``scripts/iron_god_capable_engine_runner.py`` binds
the real ones.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from . import iron_god_roadmap_refill as refill

TRANSPORT = "ENGINE"
JOBS = "capable-engine-jobs"            # beside the dispatch ledger, never inside it: the ledger holds dispatch rows only
MAX_ROUNDS = 3                          # one build and at most two repairs from the engine's own verification
ROUND_TIMEOUT_S = 30 * 60
JOB_TIMEOUT_S = 150 * 60                # the runner is failed after this even if its process still exists
MAX_INTERRUPTIONS = 5                   # a bound, so a request that is always interrupted cannot loop forever
INTERRUPTED = "INTERRUPTED"
INTERRUPTED_MARK = "EXECUTION_AUTHORITY_REVOKED"
FEEDBACK_CHARS = 6000
FILE_TOOLS = "Read,Grep,Glob,Edit,Write"
HARD_FORBIDDEN = [".github/**", ".git/**", ".claude/**"]  # workflows, repository internals and agent settings: never


def claude_argv(executable: str, prompt: str, *, resume: str | None = None, model: str | None = None) -> tuple[str, ...]:
    """The headless call: file tools only, edits accepted inside the worktree, no MCP servers, no user settings."""
    argv = [executable, "-p", prompt, "--output-format", "json", "--permission-mode", "acceptEdits",
            "--tools", FILE_TOOLS, "--strict-mcp-config", "--setting-sources="]  # "=": the runtime refuses an empty argv item
    if resume:
        argv += ["--resume", resume]
    if model:
        argv += ["--model", model]
    return tuple(argv)


def scope_problems(changed: list[str], request: dict[str, Any], graph_path: str) -> list[str]:
    """Changed paths the slice may not touch. The roadmap graph is always allowed: the builder records slices in it."""
    allowed = [*request.get("allowed_paths", []), graph_path]
    forbidden = [*HARD_FORBIDDEN, *request.get("forbidden_paths", [])]
    problems = []
    for path in sorted({p.replace("\\", "/") for p in changed}):
        if path != graph_path and refill._matches(path, forbidden):
            problems.append(f"FORBIDDEN_PATH: {path}")
        elif not refill._matches(path, allowed):
            problems.append(f"OUTSIDE_ALLOWED_PATHS: {path}")
    return problems


def repair_prompt(problems: list[str], failure: str) -> str:
    lines = ["The engine checked your change and it is not acceptable yet. Fix it by editing files; do not explain."]
    if problems:
        lines += ["You changed paths this slice may not touch. Undo those changes:", *[f"- {p}" for p in problems]]
    if failure:
        lines += ["The repository's canonical preflight failed. The end of its output:", failure[-FEEDBACK_CHARS:]]
    return "\n".join(lines)


def run_rounds(request: dict[str, Any], prompt: str, graph_path: str, *,
               ask: Callable[[str, str | None], dict[str, Any]],
               changed_paths: Callable[[], list[str]],
               verify: Callable[[], dict[str, Any]],
               check: Callable[[], None]) -> dict[str, Any]:
    """Build, let the engine verify, repair from the engine's findings. Returns the verdict; delivery is the caller's.

    ``ask(prompt, session)`` runs one model turn and returns its parsed JSON result. ``verify()`` is the repository's
    canonical preflight ({"ok", "output_tail"}). ``check()`` raises when the owner has taken control.
    """
    session: str | None = None
    message, rounds = prompt, []
    for number in range(1, MAX_ROUNDS + 1):
        check()
        answer = ask(message, session)
        if answer.get("is_error") or not isinstance(answer.get("session_id"), str):
            return {"ok": False, "reason": f"BUILDER_CALL_FAILED: {str(answer.get('result') or answer)[:300]}",
                    "rounds": rounds}
        session = answer["session_id"]
        changed = changed_paths()
        if not changed:
            # the builder was told to change nothing when the slice is ambiguous or blocked, and to say why
            return {"ok": False, "reason": f"BUILDER_CHANGED_NOTHING: {str(answer.get('result', ''))[:300]}",
                    "rounds": rounds, "session_id": session}
        problems = scope_problems(changed, request, graph_path)
        record: dict[str, Any] = {"round": number, "changed": changed, "scope_problems": problems}
        failure = ""
        if not problems:
            check()
            result = verify()
            record["preflight_ok"] = bool(result.get("ok"))
            if result.get("ok"):
                rounds.append(record)
                return {"ok": True, "rounds": rounds, "changed": changed, "session_id": session,
                        "preflight": {k: result.get(k) for k in ("command", "returncode", "baseline_match")}}
            failure = str(result.get("output_tail", ""))
        rounds.append(record)
        message = repair_prompt(problems, failure)
    last = rounds[-1]
    reason = "CHANGES_OUTSIDE_SCOPE: " + "; ".join(last["scope_problems"]) if last["scope_problems"] \
        else "PREFLIGHT_FAILED_AFTER_REPAIR_ROUNDS"
    return {"ok": False, "reason": reason[:300], "rounds": rounds, "session_id": session}


# ---- the lane side: start a job, and fold a finished job into the dispatch ledger ---------------------------------------

def job_dir(lane_root: Path) -> Path:
    return Path(lane_root) / JOBS


def start(row: dict[str, Any], request: dict[str, Any], prompt: str, *, lane_root: Path, clone: Path, worktree: Path,
          spawn: Callable[[Path], int | None], lease_id: str | None, model: str | None = None) -> dict[str, Any]:
    """Write the job the runner reads, launch it, and return the ledger row extended with what harvest needs."""
    directory = job_dir(lane_root)
    directory.mkdir(parents=True, exist_ok=True)
    job_file = directory / f"{row['dispatch_id']}.job.json"
    result = directory / f"{row['dispatch_id']}.result.json"
    result.unlink(missing_ok=True)
    branch = f"iron-god/capable/{row['dispatch_id']}"
    job_file.write_text(json.dumps({
        "dispatch_id": row["dispatch_id"], "request": request, "prompt": prompt, "clone": str(clone),
        "base": request["exact_head"], "worktree": str(worktree), "branch": branch, "result": str(result),
        "model": model}, sort_keys=True, indent=2), encoding="utf-8")
    pid = spawn(job_file)
    return {**row, "transport": TRANSPORT, "pid": pid, "lease_id": lease_id, "result": str(result), "branch": branch,
            "session_url": ""}


def harvest(state_dir: Path, rows: list[dict[str, Any]], *, write: Callable[[Path, dict[str, Any]], None],
            ingest: Callable[..., dict[str, Any]], alive: Callable[[int], bool],
            release: Callable[[str], None] | None = None, now: float | None = None) -> list[dict[str, Any]]:
    """Finish every in-engine job whose runner is done, dead or overdue; one row per thing that changed.

    A runner that the owner's takeover killed (its result says so, or it is gone without a result) is INTERRUPTED: the
    record is kept, it still counts toward the day's budget, and it spends no attempt.
    """
    now = time.time() if now is None else now
    changed = []
    for row in rows:
        if row.get("transport") != TRANSPORT or row.get("status") != "DISPATCHED":
            continue
        ident, request_id = row["dispatch_id"], str(row.get("task_key", "")).split("#a")[0]
        result_path = Path(str(row.get("result", "")))
        outcome: dict[str, Any] | None = None
        if result_path.is_file():
            try:
                outcome = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                outcome = {"ok": False, "reason": "ENGINE_BUILD_RESULT_UNREADABLE"}
        elif now - float(row.get("started_at", 0)) > JOB_TIMEOUT_S:
            outcome = {"ok": False, "reason": "ENGINE_BUILD_TIMED_OUT"}
        elif not (isinstance(row.get("pid"), int) and alive(row["pid"])):
            outcome = {"ok": False, "reason": f"{INTERRUPTED_MARK}: the runner ended without a result"}
        if outcome is None:
            continue
        if release is not None and row.get("lease_id"):
            try:
                release(row["lease_id"])
            except Exception:  # a lease that is already gone is not a reason to lose the outcome
                pass
        reason = str(outcome.get("reason", ""))[:300]
        if outcome.get("ok") and outcome.get("pr_url"):
            ingest(state_dir, ident, outcome_kind="PRODUCT_PR", pr_url=str(outcome["pr_url"]), product_advancement=True,
                   verification="canonical preflight passed in the engine before the pull request was opened",
                   notes=f"built in the engine in {len(outcome.get('rounds', []))} round(s)")
            changed.append({"dispatch_id": ident, "id": request_id, "outcome": "PRODUCT_PR", "pr_url": outcome["pr_url"]})
        elif INTERRUPTED_MARK in reason:
            write(state_dir, {**row, "status": INTERRUPTED, "completed_at": now, "notes": reason})
            changed.append({"dispatch_id": ident, "id": request_id, "outcome": INTERRUPTED})
        else:
            ingest(state_dir, ident, outcome_kind="FAIL", notes=reason or "ENGINE_BUILD_FAILED")
            changed.append({"dispatch_id": ident, "id": request_id, "outcome": "FAIL", "reason": reason})
    return changed
