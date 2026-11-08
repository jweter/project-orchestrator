"""Iron God command center: a READ-ONLY, human-readable view of what the engine is doing and has delivered.

Owner request 2026-11-08: within five seconds of opening the dashboard, know whether Iron God is productive, exactly what
it is doing, which model is responsible, what it accomplished recently, and what prevents further progress.

Rules this module keeps:
* It only READS files the engine already writes (owner control, the product lane, its jobs, publications, the
  roadmap-refill ledger, the pipeline trace, the execution/admission records, Dev Mode) plus Ollama's own /api/ps and
  this machine's RAM/CPU/disk. It writes nothing, starts nothing and holds no orchestration state of its own.
* Nothing is invented. A source that is missing or unreadable is reported as unknown (None), never as zero.
* Every progress item carries an evidence class -- VERIFIED (a merged PR the engine recorded), PLANNED (an accepted plan
  or a drafted spec), ATTEMPTED (a build or plan that is running or was tried) or FAILED -- so plans and attempts are
  never shown as accomplishments.
* "Productive" requires a verified delivery inside the window; busy is not productive.
"""

from __future__ import annotations

import json
import os
import shutil
import urllib.request
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

SCHEMA = 1
WINDOW = timedelta(hours=24)
LANE = "iron-god-products"

# Plain English for the ledger events the engine records (roadmap-refill.json); class is how the feed colours them.
EVENT_TEXT: dict[str, tuple[str, str]] = {
    "MERGED": ("milestone", "Pull request merged"),
    "PROGRESS_OPENED": ("milestone", "Roadmap progress recorded in the capability graph"),
    "LOCAL_DECOMPOSE_PLANNED": ("progress", "The local model planned a big slice as small buildable steps"),
    "LOCAL_DECOMPOSE_STARTED": ("progress", "The local model started planning a big slice"),
    "LOCAL_DECOMPOSE_BLOCKED": ("failure", "Planning stopped for this slice; it returns to the stronger builder"),
    "LOCAL_DECOMPOSE_FAILED": ("failure", "The local model's plan was rejected"),
    "LOCAL_DECOMPOSE_WAITING_FOR_MEMORY": ("waiting", "Planning waited for memory"),
    "LOCAL_DRAFT_STARTED": ("progress", "The local model started drafting a build spec"),
    "LOCAL_DRAFT_DRAFTED": ("progress", "A build spec was drafted"),
    "LOCAL_DRAFT_FAILED": ("failure", "A build spec draft failed"),
    "LOCAL_DRAFT_WAITING_FOR_MEMORY": ("waiting", "Drafting waited for memory"),
    "ADMITTED": ("progress", "A drafted spec was admitted to the build queue"),
    "GENERATED": ("info", "A roadmap slice became a build request"),
    "CAPABLE_DISPATCHED": ("progress", "Sent to the stronger (paid) builder"),
    "CAPABLE_NOT_DISPATCHED": ("info", "Not sent to the stronger builder"),
    "CAPABLE_HELD_FOR_LOCAL_DECOMPOSITION": ("info", "Held so the local model can plan it first"),
    "CAPABILITY_BLOCKER": ("failure", "A slice cannot be built with the available capability"),
    "PROGRESS_BLOCKED": ("failure", "Recording roadmap progress was blocked"),
    "DEV_MODE_ENTER": ("recovery", "Dev Mode closed approved programs to free memory"),
    "DEV_MODE_RESTORED": ("recovery", "Dev Mode restored the programs it closed"),
    "DEV_MODE_NO_HELP": ("info", "Dev Mode could not free enough memory to help"),
    "EXHAUSTED": ("failure", "A slice spent every allowed attempt"),
}

# Lane statuses in plain English (product lane lane-status.json).
LANE_TEXT = {
    "DISPATCHED": "A build worker was started",
    "ACCEPTED_LOCAL": "A local build finished and passed its gates",
    "WAITING_FOR_MEMORY": "Waiting for enough free memory to run the model",
    "WAITING_FOR_OWNER_AUTHORITY": "The owner holds control (Give Me Control); the engine is paused",
    "WAITING_FOR_CALIBRATION": "No calibrated local model can take the next task",
    "WAITING_FOR_RESOURCES": "Waiting for machine resources (for example disk space)",
    "WAITING_FOR_RUNTIME_LEASE": "Another run holds the single model slot",
    "IDLE_NO_APPROVED_BACKLOG_ITEM": "No build is ready right now; planning and drafting continue in the background",
    "IDLE_ALL_REPOSITORIES_AWAITING_CI": "Every repository is waiting for CI on an open pull request",
    "BLOCKED_CONFLICT": "Another open pull request owns the files this task needs",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _parse(stamp: Any) -> datetime | None:
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        value = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _age_s(stamp: Any, now: datetime) -> float | None:
    when = _parse(stamp)
    return None if when is None else max(0.0, (now - when).total_seconds())


def _short_repo(repository: Any) -> str:
    return str(repository or "").split("/")[-1]


def _jobs(directory: Path) -> list[dict[str, Any]]:
    out = []
    for path in sorted(Path(directory).glob("*.json")) if Path(directory).is_dir() else []:
        data = _read_json(path)
        if isinstance(data, dict):
            data["_mtime"] = path.stat().st_mtime
            out.append(data)
    return out


def _default_ollama() -> list[dict[str, Any]] | None:
    base = os.environ.get("OLLAMA_API_BASE", "http://127.0.0.1:11434").rstrip("/")
    try:
        with urllib.request.urlopen(base + "/api/ps", timeout=2) as response:
            return list(json.load(response).get("models", []))
    except (OSError, ValueError):
        return None


def _default_machine() -> dict[str, Any]:
    out: dict[str, Any] = {"ram_free_gib": None, "ram_total_gib": None, "cpu_percent": None,
                           "disk_free_gib": None, "disk_total_gib": None}
    try:
        import psutil  # optional; the engine environment has it

        memory = psutil.virtual_memory()
        out.update(ram_free_gib=round(memory.available / 2**30, 1), ram_total_gib=round(memory.total / 2**30, 1),
                   cpu_percent=round(float(psutil.cpu_percent(interval=0.2)), 0))
    except Exception:  # noqa: BLE001 - unknown stays unknown
        pass
    try:
        usage = shutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\" if os.name == "nt" else "/")
        out.update(disk_free_gib=round(usage.free / 2**30, 1), disk_total_gib=round(usage.total / 2**30, 1))
    except OSError:
        pass
    return out


def _default_engine_processes() -> int | None:
    """How many engine processes (supervisor / laptop actor) are running; None when it cannot be checked."""
    try:
        import psutil

        count = 0
        for proc in psutil.process_iter(["name", "cmdline"]):
            line = " ".join(proc.info.get("cmdline") or [])
            if "continuous_flow_supervisor" in line or "laptop_actor_bootstrap" in line:
                count += 1
        return count
    except Exception:  # noqa: BLE001
        return None


# --- sections -------------------------------------------------------------------------------------------------------


def _engine(state_root: Path, now: datetime, processes: int | None = None) -> dict[str, Any]:
    owner = None
    try:
        from .human_ownership import AUTHORIZED, owner_control_status

        owner = owner_control_status(state_root)
        authorized = owner == AUTHORIZED
    except Exception:  # noqa: BLE001
        authorized = None
    heartbeat = _read_json(state_root / "supervisor-generation-heartbeat.json") or {}
    pin = _read_json(state_root / "supervisor-pinned-release.json") or {}
    last_switch = None
    try:
        lines = (state_root / "control-switch.log").read_text(encoding="utf-8-sig").splitlines()
        last_switch = next((line for line in reversed(lines) if line.strip()), None)
    except OSError:
        pass
    beat_age = _age_s(heartbeat.get("heartbeat_at_utc"), now)
    if authorized is None:
        mode, mode_text = "UNKNOWN", "Owner-control state could not be read"
    elif authorized:
        mode, mode_text = "TAKE_THE_WHEEL", "Take the Wheel: the engine is authorized to develop"
    else:
        mode, mode_text = "OWNER_CONTROL", "Give Me Control: the owner holds the laptop; the engine is paused"
    # The supervisor writes its heartbeat once per work burst, so a quiet minute is normal: liveness comes from the
    # engine's own processes when they can be counted, and only falls back to a generous heartbeat age.
    alive = (processes > 0) if processes is not None else (None if beat_age is None else beat_age < 900)
    return {"mode": mode, "mode_text": mode_text, "owner_status": owner, "supervisor_alive": alive, "engine_processes": processes,
            "heartbeat_age_s": None if beat_age is None else round(beat_age), "release": str(pin.get("accepted_sha") or "")[:8] or None,
            "last_control_event": last_switch}


def _model(ollama: list[dict[str, Any]] | None, busy: bool, admission: list[dict[str, Any]]) -> dict[str, Any]:
    last = admission[-1] if admission else None
    if ollama is None:
        return {"state": "UNKNOWN", "text": "Ollama did not answer", "loaded": [], "last_admission": last}
    loaded = [{"name": str(m.get("name") or m.get("model")), "size_gib": round(float(m.get("size") or 0) / 2**30, 1),
               "processor": m.get("size_vram") and "GPU" or "CPU", "expires_at": m.get("expires_at")} for m in ollama]
    if not loaded and busy:
        # the shared model door unloads after every call, so a running task spends its build/test time with no model
        return {"state": "BETWEEN_CALLS", "text": "The task is running (build or tests); no model is loaded between calls",
                "loaded": [], "last_admission": last}
    if not loaded:
        return {"state": "UNLOADED", "text": "No model in memory", "loaded": [], "last_admission": last}
    name = loaded[0]["name"]
    if busy:
        return {"state": "GENERATING", "text": f"{name} is working on the task below", "loaded": loaded, "last_admission": last}
    return {"state": "LOADED_IDLE", "text": f"{name} is loaded but no task is using it (it unloads after each call)",
            "loaded": loaded, "last_admission": last}


def _current_work(lane_dir: Path, now: datetime) -> dict[str, Any]:
    """What the engine is doing right now, from its own job files and lane status (newest running job wins)."""
    active = _read_json(lane_dir / "active.json")
    running: list[tuple[float, dict[str, Any]]] = []
    for kind, directory, stage in (("planning", "decompose-jobs", "PLANNING"), ("drafting", "draft-jobs", "DRAFTING"),
                                   ("native build", "cpp-jobs", "RUNNING")):
        for job in _jobs(lane_dir / directory):
            if str(job.get("stage")) == stage:
                running.append((float(job.get("_mtime") or 0), {**job, "_kind": kind}))
    lane = _read_json(lane_dir / "lane-status.json") or {}
    status = str(lane.get("status") or "")
    if isinstance(active, dict) and active:
        return {"busy": True, "kind": "building", "repository": active.get("repository"), "task": active.get("task_id") or active.get("packet_id"),
                "title": active.get("title"), "model": active.get("model"), "worker_pid": (active.get("worker") or {}).get("pid"),
                "since_age_s": _age_s(active.get("dispatched_at_utc"), now),
                "text": f"Building {active.get('task_id') or active.get('packet_id')} in {_short_repo(active.get('repository'))}"}
    if running:
        _mtime, job = max(running, key=lambda item: item[0])
        verb = {"planning": "Planning", "drafting": "Drafting a build spec for", "native build": "Building native code for"}[job["_kind"]]
        return {"busy": True, "kind": job["_kind"], "repository": job.get("repository"), "task": job.get("id"),
                "worker_pid": job.get("pid"), "since_age_s": _age_s(job.get("started_at"), now),
                "text": f"{verb} {job.get('id')} in {_short_repo(job.get('repository'))}"}
    text = LANE_TEXT.get(status, status or "Lane status unknown")
    if status == "BLOCKED" and lane.get("worker_status"):
        text = (f"Between builds. The last build ({lane.get('packet_id')}) in {_short_repo(lane.get('repository'))} "
                f"ended {lane.get('worker_status')}: {lane.get('worker_summary')}")
    return {"busy": False, "kind": "idle", "repository": lane.get("repository"), "task": None,
            "lane_status": status or None, "last_packet": lane.get("packet_id"), "last_result": lane.get("worker_status"),
            "last_summary": lane.get("worker_summary"), "lane_observed_age_s": _age_s(lane.get("observed_at_utc"), now),
            "text": text}


def _pr(value: Any) -> tuple[Any, Any]:
    """(number, url) from a publication's pr field, which the lane records as {"number": n, "url": ...}."""
    if isinstance(value, dict):
        return value.get("number"), value.get("url")
    return value, None


def _progress(lane_dir: Path, now: datetime) -> dict[str, Any]:
    since = now - WINDOW
    pubs = (_read_json(lane_dir / "publications.json") or {}).get("items") or {}
    merged = []
    for item in pubs.values():
        if isinstance(item, dict) and item.get("state") == "MERGED":
            number, url = _pr(item.get("pr"))
            merged.append({"evidence": "VERIFIED", "repository": item.get("repository"), "pr": number, "pr_url": url,
                           "title": str(item.get("title") or "")[:140], "merged_at_utc": item.get("merged_at_utc"),
                           "merged_sha": str(item.get("merged_sha") or "")[:8] or None})
    merged.sort(key=lambda m: str(m.get("merged_at_utc") or ""), reverse=True)
    open_prs = [{"evidence": "ATTEMPTED", "repository": i.get("repository"), "pr": _pr(i.get("pr"))[0], "pr_url": _pr(i.get("pr"))[1], "state": i.get("state"),
                 "title": str(i.get("title") or "")[:140]} for i in pubs.values()
                if isinstance(i, dict) and i.get("state") not in ("MERGED", "CLOSED", "ABANDONED")]
    state = (_read_json(lane_dir / "backlog-state.json") or {}).get("items") or {}
    builds = Counter()
    recent_failures = []
    for item_id, row in state.items():
        if not isinstance(row, dict):
            continue
        outcome = str(row.get("outcome") or "")
        updated = _parse(row.get("updated_at_utc"))
        if updated is not None and updated < since:
            continue
        attempts = int(row.get("attempts") or 0)
        builds["attempts"] += attempts
        if outcome.startswith("FAIL") or outcome.startswith("REVIEW_REQUIRED"):
            builds["failed_items"] += 1
            recent_failures.append({"evidence": "FAILED", "task": item_id, "outcome": outcome[:120], "attempts": attempts,
                                    "updated_at_utc": row.get("updated_at_utc")})
        elif outcome == "MERGED":
            builds["merged_items"] += 1
    recent_failures.sort(key=lambda f: str(f.get("updated_at_utc") or ""), reverse=True)
    plans = []
    for path in sorted((lane_dir / "decompositions").glob("*.json")) if (lane_dir / "decompositions").is_dir() else []:
        plan = _read_json(path)
        if not isinstance(plan, dict):
            continue
        plans.append({"evidence": "VERIFIED" if plan.get("status") == "DONE" else "PLANNED", "parent": plan.get("parent_id"),
                      "repository": plan.get("repository"), "status": plan.get("status"),
                      "steps": [s.get("function_name") or s.get("module_path") for s in plan.get("subtasks") or []],
                      "outstanding_criteria": list(plan.get("residual") or []), "blocker": plan.get("blocker"),
                      "created_at": plan.get("created_at")})
    plans.sort(key=lambda p: str(p.get("created_at") or ""), reverse=True)
    merged_window = [m for m in merged if (_parse(m.get("merged_at_utc")) or datetime.min.replace(tzinfo=UTC)) >= since]
    return {"window_hours": int(WINDOW.total_seconds() // 3600), "verified_deliveries": merged[:12],
            "verified_in_window": len(merged_window), "open_pull_requests": open_prs,
            "build_attempts_in_window": builds["attempts"], "failed_tasks_in_window": builds["failed_items"],
            "recent_failures": recent_failures[:10], "plans": plans[:10]}


def _waiting(lane_dir: Path) -> dict[str, Any]:
    report = _read_json(lane_dir / "pipeline-report.json")
    if not isinstance(report, dict):
        return {"observed_at_utc": None, "by_reason": None, "items": []}
    items = []
    for row in report.get("slices") or []:
        blocker = row.get("blocker") or {}
        if row.get("status") in ("BLOCKED", "STUCK", "WAITING") and blocker.get("code"):
            items.append({"repository": row.get("repository"), "slice": row.get("slice"), "level": row.get("level"),
                          "status": row.get("status"), "code": blocker.get("code"), "why": blocker.get("text"),
                          "evidence": row.get("evidence")})
    explained = {item["code"] for item in items}
    # moving work is not waiting work, and a bare status with no named blocker is not a reason
    reasons = {code: n for code, n in (report.get("by_blocker") or {}).items()
               if code not in ("PROGRESSING", "CAPABLE_BUILDER_DISPATCHED") and code in explained}
    return {"observed_at_utc": report.get("observed_at_utc"), "by_reason": reasons, "items": items}


def _activity(state_root: Path, lane_dir: Path, limit: int = 40) -> list[dict[str, Any]]:
    feed: list[dict[str, Any]] = []
    ledger = (_read_json(lane_dir / "roadmap-refill.json") or {}).get("ledger") or []
    for event in ledger[-200:]:
        if not isinstance(event, dict):
            continue
        kind = str(event.get("kind") or "")
        cls, text = EVENT_TEXT.get(kind, ("info", kind.replace("_", " ").capitalize()))
        detail = event.get("detail") if isinstance(event.get("detail"), dict) else {}
        reason = detail.get("reason") or detail.get("status")
        feed.append({"at_utc": event.get("at"), "class": cls, "kind": kind, "repository": _short_repo(event.get("repository")),
                     "item": event.get("id"), "text": text, "detail": str(reason)[:160] if reason else None})
    try:
        groups: dict[tuple[str, str], dict[str, Any]] = {}
        for line in (lane_dir / "pipeline-events.jsonl").read_text(encoding="utf-8").splitlines()[-60:]:
            if not line.strip():
                continue
            event = json.loads(line)
            key = (str(event.get("at_utc") or "")[:19], str(event.get("code")))
            group = groups.setdefault(key, {"at_utc": event.get("at_utc"), "code": event.get("code"),
                                            "text": event.get("text"), "repos": Counter(), "slices": []})
            group["repos"][_short_repo(event.get("repository"))] += 1
            group["slices"].append(event.get("slice"))
        # the trace re-reports an unchanged blocker on every pass: show each distinct blocker once, at its newest time,
        # with the time it was first seen, instead of flooding the feed
        distinct: dict[tuple[str, frozenset[str]], dict[str, Any]] = {}
        for group in sorted(groups.values(), key=lambda g: str(g["at_utc"] or "")):
            key = (str(group["code"]), frozenset(str(s) for s in group["slices"]))
            first = distinct.get(key, {}).get("first_at_utc", group["at_utc"])
            distinct[key] = {**group, "first_at_utc": first}
        for group in distinct.values():
            n = len(group["slices"])
            repeating = group["first_at_utc"] != group["at_utc"]
            feed.append({"at_utc": group["at_utc"], "class": "waiting", "kind": group["code"],
                         "repository": ", ".join(sorted(group["repos"])), "item": None,
                         "text": f"Blocker on {n} slice{'s' if n != 1 else ''}: {group['text'] or group['code']}",
                         "detail": (f"unchanged since {str(group['first_at_utc'])[11:16]} UTC; " if repeating else "")
                                   + ", ".join(str(s) for s in group["slices"][:4]) + (" ..." if n > 4 else "")})
    except (OSError, ValueError):
        pass
    # build results: the lane's own packet ledger (a failed build, a build that passed its local gates, a merge)
    packets = _read_json(lane_dir / "ledger.json") or {}
    for event in (packets.get("events") or [])[-80:]:
        if not isinstance(event, dict):
            continue
        after = str(event.get("state_after") or event.get("state") or "")
        if after not in ("BLOCKED", "ACCEPTED_LOCAL", "MERGED", "CANCELLED"):
            continue
        packet = (packets.get("packets") or {}).get(event.get("packet_id")) or {}
        task = packet.get("task") if isinstance(packet.get("task"), str) else (packet.get("task") or {}).get("id") if isinstance(packet.get("task"), dict) else None
        status = event.get("status") or packet.get("worker_status")
        cls, text = {"BLOCKED": ("failure", "Build failed its gates"),
                     "ACCEPTED_LOCAL": ("progress", "Build passed its local gates (not yet merged)"),
                     "MERGED": ("milestone", "Build merged"),
                     "CANCELLED": ("info", "Build cancelled")}[after]
        feed.append({"at_utc": event.get("observed_at"), "class": cls, "kind": "BUILD_" + after,
                     "repository": _short_repo(event.get("repository") or packet.get("repository")),
                     "item": task or event.get("packet_id"), "text": text, "detail": str(status)[:160] if status else None})
    try:
        for line in (state_root / "control-switch.log").read_text(encoding="utf-8-sig").splitlines()[-10:]:
            stamp, _, rest = line.partition(" ")
            if "CONFIRMED" in rest or "GRANTED" in rest or "REVOKED" in rest:
                feed.append({"at_utc": stamp, "class": "milestone" if "TAKE_THE_WHEEL" in rest else "info",
                             "kind": rest.split()[0] if rest.split() else "CONTROL", "repository": "", "item": None,
                             "text": "Owner control: " + rest.split()[0].replace("_", " ").lower() if rest.split() else "Owner control",
                             "detail": None})
    except OSError:
        pass
    feed.sort(key=lambda e: (_parse(e.get("at_utc")) or datetime.min.replace(tzinfo=UTC)), reverse=True)
    return feed[:limit]


def _projects(lane_dir: Path, progress: dict[str, Any]) -> list[dict[str, Any]]:
    report = _read_json(lane_dir / "pipeline-report.json") or {}
    merged_by_repo = Counter(_short_repo(m.get("repository")) for m in progress.get("verified_deliveries") or [])
    rows: dict[str, dict[str, Any]] = {}
    for row in report.get("slices") or []:
        repo = _short_repo(row.get("repository"))
        entry = rows.setdefault(repo, {"repository": repo, "slices": 0, "by_status": Counter(), "next": None, "blocked_reasons": Counter()})
        entry["slices"] += 1
        entry["by_status"][str(row.get("status"))] += 1
        blocker = (row.get("blocker") or {}).get("code")
        if blocker and row.get("status") in ("BLOCKED", "STUCK", "WAITING"):
            entry["blocked_reasons"][blocker] += 1
        if entry["next"] is None and row.get("status") == "PROGRESSING":
            entry["next"] = {"slice": row.get("slice"), "stage": row.get("stage"), "level": row.get("level")}
    out = []
    for repo, entry in sorted(rows.items()):
        out.append({"repository": repo, "slices": entry["slices"], "by_status": dict(entry["by_status"]),
                    "verified_merged_prs": merged_by_repo.get(repo, 0), "next_eligible": entry["next"],
                    "top_blocker": entry["blocked_reasons"].most_common(1)[0][0] if entry["blocked_reasons"] else None})
    return out


def _verdict(engine: dict[str, Any], work: dict[str, Any], progress: dict[str, Any], waiting: dict[str, Any]) -> dict[str, Any]:
    delivered = progress.get("verified_in_window") or 0
    failed = progress.get("failed_tasks_in_window") or 0
    hours = progress.get("window_hours")
    if engine.get("mode") == "OWNER_CONTROL":
        return {"state": "PAUSED", "headline": "Paused: you hold control", "reason": engine.get("mode_text")}
    if engine.get("supervisor_alive") is False:
        return {"state": "DOWN", "headline": "Engine not running", "reason": "The supervisor heartbeat is stale"}
    if delivered:
        return {"state": "PRODUCTIVE", "headline": f"Productive: {delivered} verified deliver{'y' if delivered == 1 else 'ies'} in the last {hours} h",
                "reason": work.get("text")}
    if failed:
        return {"state": "NOT_DELIVERING",
                "headline": f"Working, but nothing delivered: {failed} task{'s' if failed != 1 else ''} failed in the last {hours} h",
                "reason": work.get("text")}
    if work.get("busy"):
        return {"state": "WORKING", "headline": "Working; nothing merged yet in this window", "reason": work.get("text")}
    top = max((waiting.get("by_reason") or {}).items(), key=lambda kv: kv[1], default=(None, 0))[0]
    return {"state": "IDLE", "headline": "Idle: no task is running", "reason": work.get("text") + (f"; most common blocker: {top}" if top else "")}


def snapshot(state_root: Path, *, now: datetime | None = None, ollama: Callable[[], list[dict[str, Any]] | None] | None = None,
             machine: Callable[[], dict[str, Any]] | None = None,
             engine_processes: Callable[[], int | None] | None = None) -> dict[str, Any]:
    """The whole command-center view. Never raises: each section degrades to unknown on its own."""
    now = now or _now()
    state_root = Path(state_root)
    lane_dir = state_root / LANE
    out: dict[str, Any] = {"schema": SCHEMA, "observed_at_utc": now.isoformat(), "read_only": True}

    def section(name: str, build: Callable[[], Any], fallback: Any) -> Any:
        try:
            return build()
        except Exception as exc:  # noqa: BLE001 - one broken source must not blank the dashboard
            out.setdefault("errors", []).append(f"{name}: {type(exc).__name__}")
            return fallback

    engine = section("engine", lambda: _engine(state_root, now, (engine_processes or _default_engine_processes)()), {"mode": "UNKNOWN"})
    work = section("work", lambda: _current_work(lane_dir, now), {"busy": None, "text": "unknown"})
    # the shared model door's own record of admitted / waiting / cancelled runs (iron_god_execution_status.record_decision)
    admission = section("admission", lambda: list(((_read_json(lane_dir / "execution" / "door.json") or {}).get("decisions") or [])[-6:]), [])
    model = section("model", lambda: _model((ollama or _default_ollama)(), bool(work.get("busy")), admission), {"state": "UNKNOWN"})
    resources = section("resources", lambda: {**(machine or _default_machine)(), "admission": admission,
                                               "dev_mode": _dev_mode(state_root)}, {})
    progress = section("progress", lambda: _progress(lane_dir, now), {})
    waiting = section("waiting", lambda: _waiting(lane_dir), {"items": []})
    out.update(engine=engine, work=work, model=model, resources=resources, progress=progress, waiting=waiting,
               activity=section("activity", lambda: _activity(state_root, lane_dir), []),
               projects=section("projects", lambda: _projects(lane_dir, progress), []))
    out["verdict"] = section("verdict", lambda: _verdict(engine, work, progress, waiting), {"state": "UNKNOWN", "headline": "Unknown"})
    return out


def _dev_mode(state_root: Path) -> dict[str, Any]:
    from . import iron_god_dev_mode as dev

    current = dev.status(state_root / dev.STATE_NAME)
    auto = _read_json(state_root / dev.AUTO_NAME) or {}
    return {"active": bool(current.get("active")), "closed": [c.get("name") for c in current.get("closed", [])],
            "last_decision": auto.get("last_decision")}
