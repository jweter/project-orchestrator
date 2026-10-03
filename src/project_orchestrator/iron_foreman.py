"""Independent, fail-closed recovery decisions for the Foundry supervisor."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from .human_ownership import human_mode_active
from .state_io import atomic_json
from .supervisor_health import (
    evaluate_supervisor_health,
    fence_generation,
    verify_reclaim_identity,
)


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def recovery_decision(state_root: Path, *, now: datetime | None = None,
                      confirmation_seconds: int = 30) -> dict:
    """Require the same stalled generation across a bounded confirmation window."""
    current = now or datetime.now(UTC)
    if human_mode_active(state_root):
        payload = {"schema_version": 1, "status": "HUMAN_OWNED", "confirmed": False,
                   "observed_at_utc": current.isoformat()}
        atomic_json(state_root / "iron-foreman-recovery.json", payload)
        return payload
    process = _load(state_root / "supervisor-process-instance.json")
    heartbeat = _load(state_root / "supervisor-generation-heartbeat.json")
    progress = _load(state_root / "supervisor-progress.json")
    health = evaluate_supervisor_health(
        process=process, heartbeat=heartbeat, progress=progress, now=current)
    prior = _load(state_root / "iron-foreman-recovery.json")
    same = (prior.get("process_instance_id") == health.get("process_instance_id")
            and prior.get("burst_id") == health.get("burst_id"))
    confirmed = False
    first_seen = current
    if health.get("status") == "SUPERVISOR_STALLED" and same:
        try:
            first_seen = datetime.fromisoformat(prior["first_stalled_at_utc"].replace("Z", "+00:00"))
            if first_seen.tzinfo is None:
                first_seen = first_seen.replace(tzinfo=UTC)
            confirmed = (current - first_seen).total_seconds() >= confirmation_seconds
        except (KeyError, TypeError, ValueError):
            pass
    payload = {
        "schema_version": 1,
        "status": "STALL_CONFIRMED" if confirmed else (
            "STALL_CONFIRMING" if health.get("status") == "SUPERVISOR_STALLED" else "NO_RECOVERY"),
        "process_instance_id": health.get("process_instance_id"),
        "burst_id": health.get("burst_id"),
        "health_status": health.get("status"),
        "first_stalled_at_utc": (first_seen if same else current).isoformat(),
        "observed_at_utc": current.isoformat(),
        "confirmed": confirmed,
    }
    atomic_json(state_root / "iron-foreman-recovery.json", payload)
    return payload


def prepare_reclamation(state_root: Path, *, fingerprint_fn: Callable[[int | None], str | None],
                        now: datetime | None = None) -> dict:
    """Re-read state, re-prove OS identity, then fence the exact generation."""
    if human_mode_active(state_root):
        return {"status": "HUMAN_OWNED", "prepared": False}
    decision = _load(state_root / "iron-foreman-recovery.json")
    if decision.get("status") != "STALL_CONFIRMED" or decision.get("confirmed") is not True:
        return {"status": "RECLAMATION_NOT_CONFIRMED", "prepared": False}
    current = now or datetime.now(UTC)
    health = evaluate_supervisor_health(
        process=_load(state_root / "supervisor-process-instance.json"),
        heartbeat=_load(state_root / "supervisor-generation-heartbeat.json"),
        progress=_load(state_root / "supervisor-progress.json"),
        now=current,
    )
    if (health.get("process_instance_id") != decision.get("process_instance_id")
            or health.get("burst_id") != decision.get("burst_id")):
        return {"status": "GENERATION_CHANGED", "prepared": False}
    verified = verify_reclaim_identity(health, fingerprint_fn=fingerprint_fn)
    if not verified.get("verified"):
        return {**verified, "prepared": False}
    fence = fence_generation(state_root, verified, now=current)
    return {"status": "RECLAMATION_PREPARED", "prepared": True, "fence": fence}
