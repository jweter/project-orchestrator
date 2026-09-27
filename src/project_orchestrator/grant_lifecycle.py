from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, BinaryIO

from .state_io import atomic_json

GrantMutator = Callable[[dict[str, Any]], bool]


NORMAL_EXECUTION_STAGES = frozenset({
    "BASE_VERIFIED",
    "BRANCH_CREATED",
    "EXECUTING",
    "EXECUTION_FINISHED",
    "REPAIR_FINISHED",
    "PR_OPEN",
})


CANONICAL_JOB_STATES = frozenset({
    "QUEUED",
    "CLAIMED",
    "PREPARING",
    "IMPLEMENTING",
    "VERIFYING",
    "PR_OPEN",
    "CI",
    "REVIEW",
    "PROMOTING",
    "COMPLETE",
    "FAILED",
    "ABANDONED",
    "BLOCKED",
    "UNKNOWN",
})

_PREPARING_STAGES = frozenset({"BASE_VERIFIED", "BRANCH_CREATED"})
_IMPLEMENTING_STAGES = frozenset({"EXECUTING"})
_VERIFYING_STAGES = frozenset({"EXECUTION_FINISHED", "REPAIR_FINISHED", "COMMITTED"})
_CI_STATUSES = frozenset({"CI_PENDING"})
_REVIEW_STATUSES = frozenset({"REVIEW_BLOCKED", "PR_BLOCKED"})
_PROMOTING_STATUSES = frozenset({"GREEN", "PREFLIGHT_GREEN"})
_COMPLETE_OUTCOMES = frozenset({"MERGED_VERIFIED", "PRODUCT_REALITY_VERIFIED"})
_COMPLETE_RESULT_STATUSES = frozenset({
    "MERGED",
    "PRODUCT_REALITY_VERIFIED",
    "NO_OP",
    "SELF_REPAIRED_TO_NO_OP",
    "DRY_RUN",
    "NO_ELIGIBLE_REPOSITORY",
})


def canonical_job_projection(record: dict[str, Any]) -> dict[str, Any]:
    """Project legacy grant fields into one observational job-state vocabulary.

    Active ownership and its execution stage outrank stale result-status residue.
    This is deliberately read-only: lifecycle/recovery authority remains in the
    existing raw fields until each writer is migrated explicitly.
    """
    if not isinstance(record, dict):
        raise TypeError("grant record must be a dict")

    lifecycle_state = record.get("state")
    execution_stage = record.get("execution_stage")
    result_status = record.get("result_status")
    outcome_quality = record.get("outcome_quality")

    if lifecycle_state == "QUARANTINED":
        job_state = "BLOCKED"
    elif lifecycle_state == "ABANDONED":
        job_state = "ABANDONED"
    elif lifecycle_state == "FAILED":
        job_state = "FAILED"
    elif execution_stage == "PR_OPEN":
        job_state = "PR_OPEN"
    elif execution_stage in _VERIFYING_STAGES:
        job_state = "VERIFYING"
    elif execution_stage in _IMPLEMENTING_STAGES:
        job_state = "IMPLEMENTING"
    elif execution_stage in _PREPARING_STAGES:
        job_state = "PREPARING"
    elif lifecycle_state == "RUNNING":
        job_state = "IMPLEMENTING"
    elif lifecycle_state == "CLAIMED":
        job_state = "CLAIMED"
    elif lifecycle_state in {"ISSUED", "DEFERRED"}:
        # DEFERRED is retryable capacity/user-activity wait, not a terminal block.
        job_state = "QUEUED"
    elif outcome_quality in _COMPLETE_OUTCOMES:
        job_state = "COMPLETE"
    elif result_status in _COMPLETE_RESULT_STATUSES:
        job_state = "COMPLETE"
    elif result_status in _CI_STATUSES:
        job_state = "CI"
    elif result_status in _REVIEW_STATUSES:
        job_state = "REVIEW"
    elif result_status in _PROMOTING_STATUSES:
        job_state = "PROMOTING"
    elif result_status == "PR_OPEN":
        job_state = "PR_OPEN"
    elif lifecycle_state == "COMPLETED":
        # A legacy completed record without a recognized result remains unknown;
        # do not infer verified advancement from lifecycle completion alone.
        job_state = "UNKNOWN"
    else:
        job_state = "UNKNOWN"

    return {
        "job_state": job_state,
        "lifecycle_state": lifecycle_state,
        "execution_stage": execution_stage,
        "result_status": result_status,
        "outcome_quality": outcome_quality,
    }


def grant_state_root(path: Path) -> Path:
    """Return the durable state root for legacy or unique per-grant files."""
    if path.parent.name == "hands-off-execution-grants":
        return path.parent.parent
    return path.parent


def lifecycle_path_for_grant(path: Path) -> Path:
    return grant_state_root(path) / "hands-off-grant-lifecycle.json"


def lifecycle_lock_path(lifecycle: Path) -> Path:
    return lifecycle.with_name(lifecycle.name + ".lock")


def ensure_lifecycle_lock_file(lock_path: Path) -> None:
    """Create the one-byte lock target exactly once across threads/processes."""
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if lock_path.stat().st_size >= 1:
                    return
            except FileNotFoundError:
                continue
            time.sleep(0.01)
            continue
        try:
            os.write(fd, b"0")
        finally:
            os.close(fd)
        return


def acquire_lifecycle_lock(lifecycle: Path) -> BinaryIO:
    """Acquire the kernel-owned lifecycle transaction lock."""
    lock_path = lifecycle_lock_path(lifecycle)
    ensure_lifecycle_lock_file(lock_path)
    handle = lock_path.open("r+b", buffering=0)
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        while True:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                time.sleep(0.01)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


def release_lifecycle_lock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    handle.close()


def mutate_grant_lifecycle(lifecycle: Path, grant_id: str, mutator: GrantMutator) -> bool:
    """Serialize one grant-record mutation without losing concurrent updates."""
    handle = acquire_lifecycle_lock(lifecycle)
    try:
        try:
            state = json.loads(lifecycle.read_text(encoding="utf-8"))
            record = state["grants"][grant_id]
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return False
        if not isinstance(record, dict) or not mutator(record):
            return False
        atomic_json(lifecycle, state)
        return True
    finally:
        release_lifecycle_lock(handle)

def advance_claimed_execution_stage(
    lifecycle: Path,
    grant_id: str,
    stage: str,
    *,
    evidence: dict[str, Any] | None = None,
    updated_at: str,
) -> bool:
    """Advance a normal claimed grant using the shared stage vocabulary.

    Recovery-stage authority intentionally remains outside this function.
    """
    if stage not in NORMAL_EXECUTION_STAGES:
        return False
    supplied = evidence or {}

    def update(record: dict[str, Any]) -> bool:
        if record.get("state") != "CLAIMED":
            return False
        record["execution_stage"] = stage
        record["stage_updated_at"] = updated_at
        for key, value in supplied.items():
            if value not in (None, ""):
                record[key] = value
        return True

    return mutate_grant_lifecycle(lifecycle, grant_id, update)
