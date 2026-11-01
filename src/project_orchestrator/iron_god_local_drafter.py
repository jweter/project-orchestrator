"""Let the local builder draft the Level 1 roadmap slices it is measured to build (issue #594).

The refill routes each request by measured capability. A request routed ``LOCAL`` with a declared Level 1 (one new pure
function) needs a *draft* before it can become a backlog item. The planner for that already exists: the product
worker's own planning step and plan validator. ``scripts/iron_god_draft_author.py`` runs them in a separate process for
one request (the roadmap slice rides along as the planner's feedback), and this module:

* manages one persisted job per attempt (``DRAFTING -> DONE | FAILED``) - one at a time, only while the lane is idle,
  at most ``MAX_ATTEMPTS`` per request, a hung run failed by age;
* converts a validated plan into an ordinary backlog draft (``plan_to_draft``) and writes it to the drafts directory,
  where the unchanged strict admission (contract, roadmap reference, scope, route and declared level, baseline,
  reservations, duplicates) accepts or rejects it - the weak local planner is gated mechanically, never trusted;
* feeds an admission rejection back to the next attempt's prompt.

Level 2 slices (directed edits with approved failing tests) are not drafted here: they keep waiting for an externally
authored draft or a stronger builder, and are never simplified into a Level 1 function.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from . import iron_god_roadmap_refill as refill
from .state_io import atomic_json

JOBS = "draft-jobs"
RESULTS = "draft-results"
REQUEST_COPIES = "draft-requests"
MAX_ATTEMPTS = 2
DRAFT_TIMEOUT_S = 30 * 60
SUPPORTED_LEVEL = 1


def _jobs(lane_root: Path) -> list[dict[str, Any]]:
    directory = Path(lane_root) / JOBS
    rows = []
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(row, dict) and "id" in row:
            rows.append(row)
    return rows


def _job_path(lane_root: Path, ident: str, attempt: int) -> Path:
    return Path(lane_root) / JOBS / f"{ident}.a{attempt}.json"


def drafting_in_progress(lane_root: Path, *, now: float | None = None) -> bool:
    """True while a draft job is running; the lane holds new dispatch so the single local model is never shared."""
    now = time.time() if now is None else now
    return any(row["stage"] == "DRAFTING" and now - float(row.get("started_at", 0)) < DRAFT_TIMEOUT_S + 60
               for row in _jobs(lane_root))


def eligible(request: dict[str, Any]) -> bool:
    route = request.get("route") or {}
    return route.get("builder") == refill.LOCAL and request.get("capability_level") == SUPPORTED_LEVEL


def plan_to_draft(request: dict[str, Any], raw: dict[str, Any], validated: dict[str, Any]) -> dict[str, Any]:
    """A backlog draft (Level 1 build spec) from a plan the worker's validator accepted.

    Only the examples that survived validation are kept (matched by call text), with the planner's own ``why``.
    """
    usable = {" ".join(str(e["call"]).split()) for e in validated["examples"]}
    examples = [{"call": " ".join(str(e["call"]).split()), "why": " ".join(str(e.get("why", "")).split()),
                 "expected": e["expected"]} for e in raw.get("examples", [])
                if isinstance(e, dict) and "call" in e and "expected" in e and " ".join(str(e["call"]).split()) in usable]
    slice_text = f"roadmap slice {request['slice']} of capability {request['capability']}"
    return {
        "id": request["id"], "repository": request["repository"],
        "title": f"{validated['function_name']} for {request['slice']}"[:120],
        "roadmap_ref": request["roadmap_ref"], "addresses": f"{request['capability']}: {request['slice']}",
        "why": f"The owner-approved {slice_text} requires it: {request['objective']}"[:1000],
        "consumer": f"the {slice_text} and the product code that builds on it ({request['objective'][:100]})",
        "objective": validated["objective"], "interfaces": [validated["module_path"]], "depends_on": [],
        "acceptance_criteria": list(validated["acceptance_criteria"]), "tests": [validated["test_path"]],
        "level": SUPPORTED_LEVEL, "complexity": "low",
        "verification": "canonical preflight and repository CI at the exact head",
        "definition_of_done": "merged with green CI and the roadmap graph updated",
        "build": {"module_path": validated["module_path"], "function_name": validated["function_name"],
                  "signature": validated["signature"], "objective": validated["objective"], "examples": examples,
                  "note": validated.get("note", "")},
    }


def _rejection_feedback(lane_root: Path, ident: str) -> str:
    rows = [e for e in refill.load_store(lane_root)["ledger"] if e["kind"] == "REJECTED" and e["id"] == ident]
    return ", ".join(rows[-1]["detail"].get("problems", [])) if rows else ""


def _draft_state(lane_root: Path, ident: str) -> str:
    """NONE, UNJUDGED (written, not yet admitted or rejected), or JUDGED (rejected; a retry may follow)."""
    path = Path(lane_root) / refill.DRAFTS / f"{ident}.json"
    if not path.is_file():
        return "NONE"
    import hashlib

    judged = refill.load_store(lane_root)["processed"].get(ident)
    return "JUDGED" if judged == hashlib.sha256(path.read_bytes()).hexdigest() else "UNJUDGED"


def has_result_ready(lane_root: Path) -> bool:
    """A draft job whose result file has appeared: the next pass should harvest it without waiting for the interval."""
    return any(row["stage"] == "DRAFTING" and Path(row["result"]).is_file() for row in _jobs(lane_root))


def spawn_author(request_file: Path, clone: Path, base: str, result: Path) -> int | None:
    """Default launcher; the lane supplies a release-bound variant."""
    runner = Path(__file__).resolve().parents[2] / "scripts" / "iron_god_draft_author.py"
    result.parent.mkdir(parents=True, exist_ok=True)
    with result.with_suffix(".log").open("ab") as log:
        process = subprocess.Popen([sys.executable, str(runner), "--request", str(request_file), "--clone", str(clone),
                                    "--base", base, "--result", str(result)], stdout=log, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL)
    return process.pid


def advance(lane_root: Path, requests: list[dict[str, Any]], clone: Path, base: str, *,
            spawn: Callable[[Path, Path, str, Path], int | None] = spawn_author, may_start: bool = True,
            now: Callable[[], float] = time.time, extra: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Harvest finished draft jobs and start at most one new one; returns what happened, one row per request touched."""
    outcomes: list[dict[str, Any]] = []
    for job in [row for row in _jobs(lane_root) if row["stage"] == "DRAFTING"]:
        result_path = Path(job["result"])
        path = _job_path(lane_root, job["id"], job["attempt"])
        if not result_path.is_file():
            if now() - float(job["started_at"]) > DRAFT_TIMEOUT_S:
                job.update(stage="FAILED", reason="DRAFT_TIMED_OUT")
                atomic_json(path, job)
                outcomes.append({"id": job["id"], "status": "FAILED", "reason": "DRAFT_TIMED_OUT"})
            continue
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            result = {"ok": False, "reason": "DRAFT_RESULT_UNREADABLE"}
        request = next((r for r in requests if r["id"] == job["id"]), None)
        if result.get("ok") and request is not None:
            draft = plan_to_draft(request, result["plan"], result["validated"])
            target = Path(lane_root) / refill.DRAFTS / f"{job['id']}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(target, draft)
            job.update(stage="DONE")
            outcomes.append({"id": job["id"], "status": "DRAFTED"})
        elif result.get("ok"):
            job.update(stage="FAILED", reason="REQUEST_NO_LONGER_CURRENT")  # main moved on; nothing is written
            outcomes.append({"id": job["id"], "status": "FAILED", "reason": "REQUEST_NO_LONGER_CURRENT"})
        else:
            job.update(stage="FAILED", reason=str(result.get("reason", "DRAFT_FAILED"))[:300])
            outcomes.append({"id": job["id"], "status": "FAILED", "reason": job["reason"]})
        atomic_json(path, job)
    if not may_start or drafting_in_progress(lane_root, now=now()):
        return outcomes
    for request in requests:
        if not eligible(request) or _draft_state(lane_root, request["id"]) == "UNJUDGED":
            continue
        attempt = sum(1 for row in _jobs(lane_root) if row["id"] == request["id"])
        if attempt >= MAX_ATTEMPTS:
            continue  # two local attempts failed or were rejected: a stronger builder or a human decides next
        guided = {**request, **(extra or {}), "planner_feedback": _rejection_feedback(lane_root, request["id"])}
        request_file = Path(lane_root) / REQUEST_COPIES / f"{request['id']}.a{attempt}.json"
        atomic_json(request_file, guided)
        result = Path(lane_root) / RESULTS / f"{request['id']}.a{attempt}.json"
        result.unlink(missing_ok=True)
        pid = spawn(request_file, clone, base, result)
        atomic_json(_job_path(lane_root, request["id"], attempt),
                    {"id": request["id"], "attempt": attempt, "stage": "DRAFTING", "started_at": now(), "pid": pid,
                     "result": str(result), "base": base})
        outcomes.append({"id": request["id"], "status": "STARTED", "attempt": attempt})
        break  # one local-model job at a time
    return outcomes
