"""Where every roadmap slice is in the road from the owner's roadmap to merged code, and exactly what is stopping it.

The owner's roadmap says WHAT to build; the engine derives slices, routes them, decomposes the big ones with the local model,
builds the pieces and delivers them. Any stage can stall for a reason nobody predicted. This module reads what the engine already
records (the request files, the decomposition plans and jobs, the capable-builder dispatches, the ledger) and, for each
roadmap slice, says:

* its STAGE (routed, decomposing, decomposed, dispatched, local build, ...),
* its STATUS (progressing, waiting, blocked, stuck, done),
* the named BLOCKER with plain-language text and the evidence it rests on.

Named triggers are emitted ONCE when they first occur (and again only if the reason changes) to ``pipeline-events.jsonl``, so a gap
is reported by the engine at the moment it happens instead of discovered later. A request that has not moved for ``STUCK_AFTER_S``
and that no named rule explains is reported as ``UNEXPLAINED_NO_PROGRESS`` together with everything the engine knows about it, so
an unknown gap still produces evidence.

Read-only with respect to the work: it never changes a request, a plan or a job.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import iron_god_roadmap_refill as refill
from . import iron_god_task_decomposer as decomposer
from .state_io import atomic_json

REPORT = "pipeline-report.json"
EVENTS = "pipeline-events.jsonl"
STUCK_AFTER_S = 30 * 60  # no recorded movement for this long, with nothing explaining it, is reported as unexplained
EVENT_LOG_LIMIT = 400

# plain-language meaning of every named blocker; the code is what the engine emits, the text is what the owner reads
BLOCKERS = {
    "SLICE_CAPABILITY_LEVEL_UNDECLARED": "The capability graph declares no capability_level for this slice, so it cannot be routed. The graph needs a level.",
    "DECOMPOSITION_NOT_STARTED": "A big slice is waiting for the local model to break it into small pieces, but no decomposition job has started.",
    "DECOMPOSITION_WAITING_FOR_MEMORY": "Decomposition needs the local model and there is not enough free memory to load it yet.",
    "DECOMPOSITION_PLAN_REJECTED": "The local model's breakdown was rejected by the plan validator; the reason is in the evidence.",
    "DECOMPOSITION_ATTEMPTS_EXHAUSTED": "The local model's breakdown failed every allowed attempt.",
    "DECOMPOSITION_NOT_POSSIBLE": "The slice cannot be decomposed locally (a terminal reason is recorded).",
    "WAITING_FOR_CAPABLE_BUILDER_BUDGET": "The slice needs the stronger builder and today's budget is used up.",
    "CAPABLE_BUILDER_DISPATCHED": "The slice was handed to the stronger builder and is in progress there.",
    "LOCAL_BUILD_EXHAUSTED": "The local builder spent every allowed attempt on this slice.",
    "REPOSITORY_HEAD_MOVED": "The repository moved on since this request was derived; it is being re-derived.",
    "UNEXPLAINED_NO_PROGRESS": "Nothing has moved for a long time and no known rule explains it. The evidence is attached so it can be diagnosed.",
}
DONE_EVENTS = {"PROGRESS_OPENED", "MERGED"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _epoch(stamp: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def gather(lane_root: Path) -> dict[str, Any]:
    """Everything the classifier needs, read once. Nothing here changes any state."""
    root = Path(lane_root)
    requests = []
    for path in sorted((root / refill.REQUESTS).glob("*.json")) if (root / refill.REQUESTS).is_dir() else []:
        data = _read_json(path)
        if isinstance(data, dict) and data.get("id"):
            data["_file_mtime"] = path.stat().st_mtime
            requests.append(data)
    jobs: dict[str, list[dict[str, Any]]] = {}
    try:
        for row in decomposer._jobs(root):
            jobs.setdefault(str(row.get("id")), []).append(row)
    except Exception:  # noqa: BLE001 - an unreadable job table is itself reported as "no jobs"
        pass
    plans = {r["id"] for r in requests if decomposer.plan_path(root, r["id"]).is_file()}
    events: dict[str, list[dict[str, Any]]] = {}
    for event in refill.load_store(root).get("ledger", []):
        if isinstance(event, dict):
            events.setdefault(str(event.get("id")), []).append(event)
    dispatched: dict[str, str] = {}
    directory = root / "capable-dispatches"
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        data = _read_json(path)
        if isinstance(data, dict):
            key = str(data.get("task_key") or data.get("request_id") or "").split("#", 1)[0]
            if key:
                dispatched[key] = str(data.get("at") or data.get("dispatched_at_utc") or "")
    backlog_state = _read_json(root / "backlog-state.json") or {}
    return {"requests": requests, "jobs": jobs, "plans": plans, "events": events, "dispatched": dispatched,
            "backlog": backlog_state.get("items", {}) if isinstance(backlog_state, dict) else {}}


def _level(request: dict[str, Any]) -> int | None:
    value = (request.get("route") or {}).get("level", request.get("capability_level"))
    return value if type(value) is int else None


def _last_movement(request: dict[str, Any], ctx: dict[str, Any]) -> float:
    stamps = [float(request.get("_file_mtime") or 0.0)]
    stamps += [t for e in ctx["events"].get(str(request["id"]), []) if (t := _epoch(e.get("at"))) is not None]
    stamps += [float(j.get("updated_at") or j.get("at_epoch") or 0.0) for j in ctx["jobs"].get(str(request["id"]), [])]
    return max(stamps)


def classify(request: dict[str, Any], ctx: dict[str, Any], *, now: float, free_gib: float | None = None,
             need_gib: float | None = None) -> dict[str, Any]:
    """One roadmap request -> {stage, status, blocker, evidence}. Pure: all inputs are passed in."""
    rid = str(request["id"])
    route = request.get("route") if isinstance(request.get("route"), dict) else {}
    level = _level(request)
    builder = route.get("builder")
    events = ctx["events"].get(rid, [])
    last_kind = events[-1].get("kind") if events else None
    jobs = ctx["jobs"].get(rid, [])
    row: dict[str, Any] = {"id": rid, "repository": request.get("repository"), "slice": request.get("slice"), "level": level,
                           "builder": builder, "route_reason": route.get("reason"), "stage": "ROUTED", "status": "WAITING",
                           "blocker": None, "evidence": {}}

    def block(code: str, stage: str, status: str = "BLOCKED", **evidence: Any) -> dict[str, Any]:
        row.update(stage=stage, status=status, blocker={"code": code, "text": BLOCKERS[code]}, evidence=evidence)
        return row

    planning = request.get("slice") == refill.PLANNING_SLICE or str(route.get("reason")) == "ROADMAP_PLANNING_TASK"
    if level is None and not planning:
        return block("SLICE_CAPABILITY_LEVEL_UNDECLARED", "ROUTING", route_reason=route.get("reason"))
    if request.get("head_is_current") is False:
        return block("REPOSITORY_HEAD_MOVED", "ROUTING", "WAITING")
    if planning:
        row.update(stage="PLANNING_TASK")
        if rid in ctx["dispatched"]:
            row.update(status="PROGRESSING", blocker={"code": "CAPABLE_BUILDER_DISPATCHED", "text": BLOCKERS["CAPABLE_BUILDER_DISPATCHED"]},
                       evidence={"dispatched_at": ctx["dispatched"][rid]})
        elif any((e.get("detail") or {}).get("reason") == "DAILY_BUDGET_EXHAUSTED" for e in events if isinstance(e.get("detail"), dict)):
            return block("WAITING_FOR_CAPABLE_BUILDER_BUDGET", "PLANNING_TASK", "WAITING")
        return row

    if builder == refill.LOCAL:
        row.update(stage="LOCAL_BUILD", status="PROGRESSING")
        state = ctx["backlog"].get(rid, {}) if isinstance(ctx["backlog"], dict) else {}
        if int(state.get("attempts", 0) or 0) >= 3 or last_kind == "EXHAUSTED":
            return block("LOCAL_BUILD_EXHAUSTED", "LOCAL_BUILD", attempts=state.get("attempts"), outcome=str(state.get("outcome"))[:200])
        return row

    # a slice routed to the stronger builder because of its size is decomposed locally first
    if decomposer.decomposable({**request, "capability_level": level}):
        if rid in ctx["plans"]:
            row.update(stage="DECOMPOSED", status="PROGRESSING")
            children = [c for c in ctx["requests"] if str(c.get("parent") or c.get("parent_id") or "") == rid]
            row["evidence"] = {"children": len(children)}
            return row
        if jobs:
            last = jobs[-1]
            reason = str(last.get("reason") or "")
            spent = sum(1 for j in jobs if j.get("stage") != "INTERRUPTED")
            if any(m in reason for m in decomposer.TERMINAL_MARKERS):
                return block("DECOMPOSITION_NOT_POSSIBLE", "DECOMPOSITION", reason=reason[:300])
            if spent >= decomposer.MAX_PLAN_ATTEMPTS and last.get("stage") == "FAILED":
                return block("DECOMPOSITION_ATTEMPTS_EXHAUSTED", "DECOMPOSITION", attempts=spent, last_reason=reason[:300])
            if "ModelNotAdmitted" in reason or "MEMORY" in reason:
                return block("DECOMPOSITION_WAITING_FOR_MEMORY", "DECOMPOSITION", "WAITING", free_gib=free_gib, needs_gib=need_gib)
            if last.get("stage") == "FAILED" and "REJECTED" in reason:
                return block("DECOMPOSITION_PLAN_REJECTED", "DECOMPOSITION", "BLOCKED", attempts=spent, last_reason=reason[:300])
            row.update(stage="DECOMPOSITION", status="PROGRESSING", evidence={"job_stage": last.get("stage")})
            return row
        if free_gib is not None and need_gib is not None and free_gib < need_gib:
            return block("DECOMPOSITION_WAITING_FOR_MEMORY", "DECOMPOSITION", "WAITING", free_gib=free_gib, needs_gib=need_gib)
        stuck_for = now - _last_movement(request, ctx)
        return block("DECOMPOSITION_NOT_STARTED", "DECOMPOSITION", "STUCK" if stuck_for > STUCK_AFTER_S else "WAITING",
                     idle_minutes=round(stuck_for / 60.0, 1))

    # everything else is the stronger builder's: dispatched, or waiting on its budget
    if rid in ctx["dispatched"]:
        row.update(stage="CAPABLE_BUILDER", status="PROGRESSING",
                   blocker={"code": "CAPABLE_BUILDER_DISPATCHED", "text": BLOCKERS["CAPABLE_BUILDER_DISPATCHED"]},
                   evidence={"dispatched_at": ctx["dispatched"][rid]})
        return row
    if any(isinstance(e.get("detail"), dict) and e["detail"].get("reason") == "DAILY_BUDGET_EXHAUSTED" for e in events):
        return block("WAITING_FOR_CAPABLE_BUILDER_BUDGET", "CAPABLE_BUILDER", "WAITING")
    stuck_for = now - _last_movement(request, ctx)
    if stuck_for > STUCK_AFTER_S:  # nothing we know of explains it: report everything we know, once
        return block("UNEXPLAINED_NO_PROGRESS", "UNKNOWN", "STUCK", idle_minutes=round(stuck_for / 60.0, 1), builder=builder,
                     route=route, last_events=[{"kind": e.get("kind"), "at": e.get("at")} for e in events[-5:]],
                     has_plan=rid in ctx["plans"], jobs=len(jobs), decomposable=False,
                     allowed_paths=bool(request.get("allowed_paths")), has_acceptance=bool(request.get("slice_acceptance")))
    return row


def scan(lane_root: Path, *, now: float | None = None, free_gib: float | None = None, need_gib: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    ctx = gather(lane_root)
    rows = [classify(request, ctx, now=now, free_gib=free_gib, need_gib=need_gib) for request in ctx["requests"]]
    counts: dict[str, int] = {}
    for row in rows:
        key = (row["blocker"] or {}).get("code") or row["status"]
        counts[key] = counts.get(key, 0) + 1
    return {"schema_version": 1, "observed_at_utc": _now_iso(), "requests": len(rows), "by_blocker": dict(sorted(counts.items())),
            "slices": rows}


def emit_new_triggers(lane_root: Path, report: dict[str, Any]) -> list[dict[str, Any]]:
    """Append an event the FIRST time each (request, blocker) appears, and again only if its reason changes."""
    root = Path(lane_root)
    path = root / EVENTS
    seen: dict[str, str] = {}
    lines: list[str] = []
    if path.is_file():
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-EVENT_LOG_LIMIT:]
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            seen[str(row.get("id"))] = f'{row.get("code")}|{row.get("fingerprint")}'
    fresh = []
    for row in report["slices"]:
        blocker = row.get("blocker")
        if not blocker or blocker["code"] in {"CAPABLE_BUILDER_DISPATCHED"}:
            continue
        fingerprint = json.dumps(row.get("evidence"), sort_keys=True, default=str)[:300]
        key = f'{blocker["code"]}|{fingerprint}'
        if seen.get(str(row["id"])) == key:
            continue
        fresh.append({"at_utc": _now_iso(), "id": row["id"], "repository": row["repository"], "slice": row["slice"], "level": row["level"],
                      "stage": row["stage"], "status": row["status"], "code": blocker["code"], "text": blocker["text"],
                      "fingerprint": fingerprint, "evidence": row["evidence"]})
    if fresh:
        root.mkdir(parents=True, exist_ok=True)
        kept = lines + [json.dumps(e, default=str) for e in fresh]
        path.write_text("\n".join(kept[-EVENT_LOG_LIMIT:]) + "\n", encoding="utf-8")
    return fresh


def refresh(lane_root: Path, *, free_gib: float | None = None, need_gib: float | None = None, now: float | None = None) -> dict[str, Any]:
    """Scan, write ``pipeline-report.json`` for the dashboard, and emit any new trigger events. Best effort for the caller."""
    report = scan(lane_root, now=now, free_gib=free_gib, need_gib=need_gib)
    report["new_triggers"] = [{"id": e["id"], "code": e["code"]} for e in emit_new_triggers(lane_root, report)]
    atomic_json(Path(lane_root) / REPORT, report)
    return report
