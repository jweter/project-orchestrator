"""Fail-closed progress health for the Foundry supervisor generation."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .recovery_manager import process_start_fingerprint
from .state_io import atomic_json


def _stamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def evaluate_supervisor_health(*, process: dict, heartbeat: dict, progress: dict,
                               now: datetime | None = None,
                               heartbeat_timeout_seconds: int = 120,
                               progress_timeout_seconds: int = 600) -> dict:
    """Classify one generation without treating PID existence as health."""
    current = now or datetime.now(UTC)
    instance = process.get("process_instance_id")
    burst = process.get("burst_id")
    worker = process.get("worker") if isinstance(process.get("worker"), dict) else {}
    if not instance or not burst or not worker.get("pid") or not worker.get("process_start_fingerprint"):
        return {"status": "IDENTITY_UNPROVEN", "reclaimable": False}
    if heartbeat.get("process_instance_id") != instance or heartbeat.get("burst_id") != burst:
        return {"status": "HEARTBEAT_IDENTITY_MISMATCH", "reclaimable": False,
                "process_instance_id": instance, "burst_id": burst}
    heartbeat_at = _stamp(heartbeat.get("heartbeat_at_utc"))
    if heartbeat_at is None:
        return {"status": "HEARTBEAT_UNREADABLE", "reclaimable": False,
                "process_instance_id": instance, "burst_id": burst}
    heartbeat_age = (current - heartbeat_at).total_seconds()
    if heartbeat_age < 0:
        return {"status": "CLOCK_SKEW", "reclaimable": False,
                "process_instance_id": instance, "burst_id": burst}
    progress_at = _stamp(progress.get("observed_at_utc"))
    progress_matches = (progress.get("process_instance_id") == instance
                        and progress.get("burst_id") == burst
                        and isinstance(progress.get("sequence"), int))
    progress_age = (current - progress_at).total_seconds() if progress_matches and progress_at else None
    if progress_age is None:
        return {"status": "PROGRESS_UNREADABLE", "reclaimable": False,
                "process_instance_id": instance, "burst_id": burst}
    if progress_age < 0:
        return {"status": "CLOCK_SKEW", "reclaimable": False,
                "process_instance_id": instance, "burst_id": burst}
    stalled_heartbeat = heartbeat_age > heartbeat_timeout_seconds
    stalled_progress = progress_age > progress_timeout_seconds
    if stalled_heartbeat and stalled_progress:
        status = "SUPERVISOR_STALLED"
        reclaimable = True
    elif stalled_heartbeat:
        status = "HEARTBEAT_STALE_PROGRESS_RECENT"
        reclaimable = False
    elif stalled_progress:
        status = "PROGRESS_STALLED"
        reclaimable = False
    elif heartbeat.get("status") == "STATE_PUBLICATION_BLOCKED":
        status = "DEGRADED_PUBLICATION_BLOCKED"
        reclaimable = False
    else:
        status = "HEALTHY"
        reclaimable = False
    return {"status": status, "reclaimable": reclaimable,
            "process_instance_id": instance, "burst_id": burst,
            "pid": worker["pid"], "process_start_fingerprint": worker["process_start_fingerprint"],
            "heartbeat_age_seconds": heartbeat_age, "progress_age_seconds": progress_age}


def verify_reclaim_identity(health: dict, *, fingerprint_fn: Callable[[int | None], str | None] = process_start_fingerprint) -> dict:
    """Re-prove the exact OS process immediately before any recovery mutation."""
    if health.get("status") != "SUPERVISOR_STALLED" or health.get("reclaimable") is not True:
        return {"status": "RECLAIM_NOT_AUTHORIZED", "verified": False}
    try:
        pid = int(health["pid"])
    except (KeyError, TypeError, ValueError):
        return {"status": "PROCESS_IDENTITY_UNPROVEN", "verified": False}
    expected = health.get("process_start_fingerprint")
    actual = fingerprint_fn(pid)
    if not expected or actual is None or actual != expected:
        return {"status": "PROCESS_IDENTITY_CHANGED", "verified": False,
                "pid": pid, "expected_fingerprint": expected, "actual_fingerprint": actual}
    return {"status": "RECLAIM_IDENTITY_VERIFIED", "verified": True,
            "pid": pid, "process_start_fingerprint": expected,
            "process_instance_id": health.get("process_instance_id"), "burst_id": health.get("burst_id")}


def fence_generation(state_root: Path, verified: dict, *, now: datetime | None = None) -> dict:
    """Persist exact-generation revocation before any process retirement attempt."""
    if verified.get("status") != "RECLAIM_IDENTITY_VERIFIED" or verified.get("verified") is not True:
        raise ValueError("generation identity is not verified for reclamation")
    payload = {
        "schema_version": 1, "status": "GENERATION_FENCED",
        "process_instance_id": verified["process_instance_id"],
        "burst_id": verified["burst_id"], "pid": verified["pid"],
        "process_start_fingerprint": verified["process_start_fingerprint"],
        "fenced_at_utc": (now or datetime.now(UTC)).isoformat(),
    }
    atomic_json(state_root / "supervisor-generation-fence.json", payload)
    return payload


def retire_verified_generation(verified: dict, *, run_fn=None,
                               fingerprint_fn: Callable[[int | None], str | None] = process_start_fingerprint) -> dict:
    """Boundedly retire only the exact OS process whose identity was verified."""
    import subprocess
    if verified.get("status") != "RECLAIM_IDENTITY_VERIFIED" or verified.get("verified") is not True:
        return {"status": "RETIRE_NOT_AUTHORIZED", "retired": False}
    pid=int(verified["pid"])
    expected=verified.get("process_start_fingerprint")
    if not expected or fingerprint_fn(pid) != expected:
        return {"status": "PROCESS_IDENTITY_CHANGED", "retired": False, "pid": pid}
    runner=run_fn or subprocess.run
    try:
        result=runner(["taskkill","/PID",str(pid),"/T","/F"],
                     text=True,capture_output=True,check=False,timeout=10)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"status": "PROCESS_RETIRE_UNCONFIRMED", "retired": False,
                "pid": pid, "error": type(exc).__name__}
    if result.returncode != 0:
        return {"status": "PROCESS_RETIRE_FAILED", "retired": False, "pid": pid}
    # A missing fingerprint is ambiguous (dead or unreadable). Require the OS
    # kill operation itself to have succeeded; do not infer death from a
    # failed/unknown fingerprint probe after an unsuccessful taskkill.
    after=fingerprint_fn(pid)
    if after is not None:
        return {"status": "PROCESS_STILL_ALIVE", "retired": False, "pid": pid}
    return {"status": "PROCESS_RETIRED", "retired": True, "pid": pid}


def generation_is_fenced(state_root: Path, *, process_instance_id: str, burst_id: str) -> bool:
    try:
        import json
        payload = json.loads((state_root / "supervisor-generation-fence.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    return (payload.get("status") == "GENERATION_FENCED"
            and payload.get("process_instance_id") == process_instance_id
            and payload.get("burst_id") == burst_id)
