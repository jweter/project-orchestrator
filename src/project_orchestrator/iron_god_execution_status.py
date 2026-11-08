"""What Iron God is doing right now, in one merged view for the dashboard.

Several processes take part in one development turn (the supervisor's lane, the worker or the C++ author, the guarded
Ollama door, the delivery path). Each writes its own small JSON file under ``<lane root>/execution/`` with an atomic
replace, so there is no shared lock and a crash can never corrupt another writer's file. ``view`` merges them and adds the
live facts only the dashboard host can read (free RAM, which model Ollama has loaded).

Nothing here decides anything: it only records and reports. A missing or unreadable file is reported as unknown, never as
healthy. Phases: IDLE, WAITING, BLOCKED, PLANNING, CODING, TESTING, COMPILING, VERIFYING, DELIVERING.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .state_io import atomic_json

SCHEMA = 1
PHASES = ("IDLE", "WAITING", "BLOCKED", "PLANNING", "CODING", "TESTING", "COMPILING", "VERIFYING", "DELIVERING")
ACTIVE_PHASES = {"PLANNING", "CODING", "TESTING", "COMPILING", "VERIFYING", "DELIVERING"}
ENV_DIR = "IRON_GOD_EXECUTION_DIR"
STALE_AFTER_S = 900.0  # a worker that has written nothing for this long is reported as possibly stuck, not as progressing
DECISIONS_KEPT = 8

# Lane statuses that mean "waiting" (a condition will clear) versus "blocked" (a person or a fix is needed).
WAITING_TEXT = {
    "WAITING_FOR_CALIBRATION": "The local model has not been calibrated for this machine yet, so no local work is dispatched.",
    "WAITING_FOR_MEMORY": "Not enough free memory to load the model beside Windows; it will start when memory is available.",
    "NO_AUTHORIZED_WORK": "Nothing in the owner-approved roadmap is ready to build.",
    "IDLE_NO_APPROVED_BACKLOG_ITEM": "No approved backlog item is ready to build.",
    "NO_READY_EXECUTION_SLICE": "No slice is ready to execute.",
    "SLOT_BUSY": "Another model run holds the single model slot.",
    "MEMORY": "Not enough free memory to admit the model right now.",
    "CALIBRATION_STALE_MODEL": "The capability profile was measured for a different model than the configured builder.",
    "CALIBRATION_REQUIRED": "No capability profile exists for this machine.",
    "SLICE_EXCEEDS_CALIBRATED_CAPABILITY": "The slice needs a higher capability level than the calibrated model has shown.",
}
BLOCKED_STATUS_HINTS = ("BLOCKED", "FAILED", "REJECTED", "REFUSED", "UNVERIFIED", "UNAVAILABLE", "EXHAUSTED")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def execution_dir(lane_root: Path) -> Path:
    return Path(lane_root) / "execution"


def dir_from_env() -> Path | None:
    value = os.environ.get(ENV_DIR)
    return Path(value) if value else None


def note(directory: Path | None, source: str, **fields: Any) -> None:
    """Replace ``<directory>/<source>.json``. Best effort by design: reporting must never break the work it reports on."""
    if directory is None:
        return
    try:
        directory.mkdir(parents=True, exist_ok=True)
        atomic_json(directory / f"{source}.json", {"schema_version": SCHEMA, "source": source,
                                                    "updated_at_utc": _now(), "updated_at_epoch": time.time(), **fields})
    except (OSError, ValueError, TypeError):
        pass


def _read(directory: Path, source: str) -> dict[str, Any] | None:
    import json

    try:
        data = json.loads((directory / f"{source}.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class Trace:
    """Phase reporting for one worker turn; a no-op when no execution directory is configured."""

    def __init__(self, directory: Path | None, **context: Any) -> None:
        self.directory, self.context = directory, context
        self.started = time.time()

    def phase(self, phase: str, detail: str = "") -> None:
        if phase not in PHASES:
            raise ValueError(f"unknown phase {phase}")
        note(self.directory, "worker", phase=phase, detail=detail[:300], started_at_epoch=self.started,
             pid=os.getpid(), **self.context)

    def wrap(self, phase: str, function: Callable[..., Any] | None) -> Callable[..., Any] | None:
        """``function`` that first records ``phase``; None stays None so optional hooks keep their meaning."""
        if function is None:
            return None

        def traced(*args: Any, **kwargs: Any) -> Any:
            self.phase(phase)
            return function(*args, **kwargs)

        return traced


def record_decision(kind: str, model: str, admitted: bool, why: str, *, available_gib: float | None, required_gib: float | None) -> None:
    """The model door's admission decisions (admitted, waiting for memory, slot busy, cancelled on pressure), newest last."""
    directory = dir_from_env()
    if directory is None:
        return
    previous = (_read(directory, "door") or {}).get("decisions") or []
    row = {"at_utc": _now(), "kind": kind, "model": model, "admitted": admitted, "why": why[:240],
           "available_gib": None if available_gib is None else round(available_gib, 2),
           "required_gib": None if required_gib is None else round(required_gib, 2)}
    if previous and all(previous[-1].get(k) == row.get(k) for k in ("kind", "model", "admitted", "why")):
        previous[-1] = row  # a wait that repeats every poll is one line, not a flood
    else:
        previous.append(row)
    note(directory, "door", decisions=previous[-DECISIONS_KEPT:], model=model)


def _age(epoch: Any, now: float) -> float | None:
    return round(max(0.0, now - float(epoch)), 1) if isinstance(epoch, (int, float)) else None


def explain(code: str | None) -> str:
    if not code:
        return ""
    key = str(code).split(":", 1)[0].strip()
    return WAITING_TEXT.get(key, "")


def view(lane_root: Path, *, now: float | None = None, loaded_models: list[str] | None = None,
         available_gib: float | None = None, capability_profile: dict[str, Any] | None = None,
         owner_authorized: bool | None = None, builder_model: str | None = None) -> dict[str, Any]:
    """The merged picture the dashboard shows. Unknown stays unknown; a stale worker is flagged, never shown as healthy."""
    now = time.time() if now is None else now
    directory = execution_dir(lane_root)
    lane = _read(directory, "lane") or {}
    worker = _read(directory, "worker") or {}
    door = _read(directory, "door") or {}
    delivery = _read(directory, "delivery") or {}
    active = lane.get("active") if isinstance(lane.get("active"), dict) else None
    lane_status = str(lane.get("status") or "")
    lane_reason = str(lane.get("reason") or "")

    worker_age = _age(worker.get("updated_at_epoch"), now)
    if active:
        worker_current = worker_age is not None and str(worker.get("packet_id") or active.get("packet_id")) == str(active.get("packet_id"))
    else:  # a native C++ job has no lane record: a recent, still-active worker report stands in for it
        worker_current = (worker_age is not None and worker_age < STALE_AFTER_S and str(worker.get("phase")) in ACTIVE_PHASES)
        if worker_current:
            active = {"packet_id": worker.get("packet_id"), "repository": worker.get("repository"), "task_id": worker.get("task_id"),
                      "title": worker.get("title"), "capability_level": worker.get("capability_level"), "model": worker.get("model"),
                      "worker_pid": worker.get("pid")}
    delivery_age = _age(delivery.get("updated_at_epoch"), now)

    phase, detail = "IDLE", ""
    if worker_current:
        phase, detail = str(worker.get("phase") or "CODING"), str(worker.get("detail") or "")
    elif active:
        phase, detail = "CODING", "worker started; no phase reported yet"
    elif delivery and delivery_age is not None and delivery_age < STALE_AFTER_S and delivery.get("phase") == "DELIVERING":
        phase, detail = "DELIVERING", str(delivery.get("detail") or "")
    elif lane_status:
        waiting_like = lane_status.startswith("WAITING") or lane_status in WAITING_TEXT or lane_status.startswith(("IDLE", "NO_"))
        blocked_like = any(h in lane_status for h in BLOCKED_STATUS_HINTS)
        phase = "BLOCKED" if blocked_like and not waiting_like else "WAITING" if waiting_like else "IDLE"
        detail = lane_reason or lane_status

    reason_code = lane_reason or lane_status
    waiting = blocked = None
    if phase == "WAITING":
        waiting = {"code": reason_code, "text": explain(lane_reason) or explain(lane_status) or reason_code}
    elif phase == "BLOCKED":
        blocked = {"code": reason_code, "text": explain(lane_reason) or explain(lane_status) or reason_code,
                   "detail": str(lane.get("detail") or "")[:300]}
    decisions = list(door.get("decisions") or [])
    if decisions and not decisions[-1].get("admitted") and phase in {"IDLE", "WAITING"} and not waiting:
        last = decisions[-1]
        waiting = {"code": last.get("kind"), "text": explain(last.get("kind")) or str(last.get("why") or "")}
        phase = "WAITING"
    started_epoch = worker.get("started_at_epoch") if worker_current else None

    model_name = (worker.get("model") if worker_current else None) or (active or {}).get("model") or door.get("model") or builder_model
    level = (active or {}).get("capability_level") or lane.get("capability_level")
    max_level = (capability_profile or {}).get("max_passing_level")
    stuck = bool(worker_current and phase in ACTIVE_PHASES and worker_age is not None and worker_age > STALE_AFTER_S)
    return {
        "schema_version": SCHEMA, "observed_at_epoch": now, "phase": phase, "detail": detail,
        "active": bool(active) and phase in ACTIVE_PHASES, "stuck": stuck,
        "owner_authorized": owner_authorized,
        "repository": (active or {}).get("repository") or lane.get("repository"),
        "task": {"id": (active or {}).get("task_id") or (active or {}).get("packet_id") or lane.get("task_id"),
                 "title": (active or {}).get("title") or lane.get("title"),
                 "capability_level": level},
        "model": {"name": model_name, "route": (active or {}).get("route") or lane.get("route"),
                  "calibrated_for": (capability_profile or {}).get("model"),
                  "calibrated_max_level": max_level,
                  "level_ok": (None if level is None or max_level is None else int(level) <= int(max_level))},
        "worker": {"pid": (active or {}).get("worker_pid") or worker.get("pid") if (active or worker_current) else None,
                   "elapsed_s": _age(started_epoch, now), "last_progress": detail if worker_current else None,
                   "last_progress_age_s": worker_age if worker_current else None},
        "resources": {"available_gib": None if available_gib is None else round(available_gib, 2),
                      "loaded_models": loaded_models, "required_gib": (decisions[-1].get("required_gib") if decisions else None)},
        "admission": decisions, "waiting": waiting, "blocked": blocked,
        "lane": {"status": lane_status or None, "reason": lane_reason or None, "observed_at_utc": lane.get("observed_at_utc")},
    }
