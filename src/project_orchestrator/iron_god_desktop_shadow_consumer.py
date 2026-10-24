"""Shadow-only supervisor request consumer: acknowledge observation, not control.

Not wired into live supervisor. Never modifies owner/control authorization, never
starts or stops workers, never writes APPLIED/PAUSED or changes scheduled tasks.
It can *only* move REQUESTED_NOT_AUTHORIZED to OBSERVED_NOT_AUTHORIZED after
a caller supplies a consistent independent set of process/owner observations.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Mapping

from .iron_god_desktop_inbox import inspect_inbox
from .iron_god_desktop_requests import DesktopRequestError, record_observation
from .iron_god_desktop_control_proof import _timestamp


@dataclass(frozen=True)
class ShadowConsumerResult:
    status: str
    blockers: tuple[str, ...]
    command_id: str | None
    observation_recorded: bool = False
    execution_authorized: bool = False


def shadow_consume_once(
    db: sqlite3.Connection,
    *,
    controller: Mapping[str, Any],
    owner_authority: str,
    supervisor_pin: Mapping[str, Any],
    actor_pin: Mapping[str, Any],
    heartbeat: Mapping[str, Any],
    supervisor_process: Mapping[str, Any],
    process_fingerprint_fn: Callable[[int], str | None] | None = None,
    owner_verifier_fn: Callable[[], str] | None = None,
    controller_verifier_fn: Callable[[], Mapping[str, Any] | None] | None = None,
    release_head_fn: Callable[[str], str | None] | None = None,
    now: datetime | None = None,
    max_age_seconds: int = 120,
) -> ShadowConsumerResult:
    """Guard a single *observation* against stale and inconsistent evidence.

    Caller-provided owner authority and process observations MUST originate
    from independent trusted machinery. Their presence here does not create
    such authority. No control transition is possible through this API.
    """
    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("aware clock required")
    if type(max_age_seconds) is not int or max_age_seconds < 1:
        raise ValueError("positive horizon required")
    current = current.astimezone(UTC)
    inbox = inspect_inbox(db, now=current, max_age_seconds=max_age_seconds)
    if inbox.status != "PENDING_UNAUTHORIZED" or inbox.request is None:
        return ShadowConsumerResult(
            status="NOT_CONSUMED",
            blockers=(inbox.status,),
            command_id=inbox.request.command_id if inbox.request else None,
        )

    item = inbox.request
    problems: list[str] = []
    if owner_authority not in ("VERIFIED_HELD", "VERIFIED_RELEASED"):
        problems.append("OWNER_AUTHORITY_UNVERIFIED")
    # A caller-supplied status string is not independently verified authority.
    try:
        verified_owner = owner_verifier_fn() if owner_verifier_fn else None
    except Exception:
        verified_owner = None
    if verified_owner != owner_authority or verified_owner not in (
        "VERIFIED_HELD",
        "VERIFIED_RELEASED",
    ):
        problems.append("OWNER_RECHECK_UNVERIFIED")
    if (
        controller.get("mode") != "shadow_state_only"
        or controller.get("candidate_execution_authorized") is not False
    ):
        problems.append("NOT_SAFE_SHADOW_MODE")

    sha = supervisor_pin.get("accepted_sha")
    if (
        not isinstance(sha, str)
        or re.fullmatch("[0-9a-f]{40}", sha) is None
        or supervisor_pin.get("enabled") is not True
        or actor_pin.get("enabled") is not True
        or actor_pin.get("accepted_sha") != sha
        or actor_pin.get("accepted_repo") != supervisor_pin.get("accepted_repo")
    ):
        problems.append("PINNED_RELEASE_UNVERIFIED")

    generation = heartbeat.get("process_instance_id")
    timestamp = _timestamp(heartbeat.get("heartbeat_at_utc"))
    if (
        not isinstance(generation, str)
        or not generation
        or heartbeat.get("status") != "RUNNING_CURRENT"
        or heartbeat.get("actor_revision") != sha
    ):
        problems.append("SUPERVISOR_HEARTBEAT_IDENTITY_MISMATCH")
    if (
        timestamp is None
        or not 0 <= (current - timestamp).total_seconds() <= max_age_seconds
    ):
        problems.append("SUPERVISOR_HEARTBEAT_STALE")
    if (
        supervisor_process.get("process_instance_id") != generation
        or supervisor_process.get("accepted_sha") != sha
        or type(supervisor_process.get("pid")) is not int
        or supervisor_process["pid"] < 1
    ):
        problems.append("SUPERVISOR_PROCESS_IDENTITY_UNVERIFIED")

    # Independently recheck the live OS incarnation. A PID may be reused.
    pid = supervisor_process.get("pid")
    expected_fingerprint = supervisor_process.get("process_start_fingerprint")
    try:
        actual_fingerprint = (
            process_fingerprint_fn(pid)
            if process_fingerprint_fn and type(pid) is int and pid > 0
            else None
        )
    except Exception:
        actual_fingerprint = None
    if (
        not isinstance(expected_fingerprint, str)
        or not expected_fingerprint
        or actual_fingerprint != expected_fingerprint
    ):
        problems.append("PROCESS_START_FINGERPRINT_UNVERIFIED")

    # Shadow-only controller state must be re-read at the decision boundary.
    try:
        current_controller = (
            controller_verifier_fn() if controller_verifier_fn else None
        )
    except Exception:
        current_controller = None
    if (
        not isinstance(current_controller, Mapping)
        or current_controller.get("mode") != "shadow_state_only"
        or current_controller.get("candidate_execution_authorized") is not False
    ):
        problems.append("CONTROLLER_RECHECK_FAILED")

    # A pin supplied by the caller is not evidence of the real Git HEAD.
    release_repo = supervisor_pin.get("accepted_repo")
    try:
        head = (
            release_head_fn(release_repo)
            if release_head_fn and isinstance(release_repo, str)
            else None
        )
    except Exception:
        head = None
    if head != sha or not isinstance(release_repo, str) or not release_repo:
        problems.append("LIVE_RELEASE_HEAD_UNVERIFIED")

    if problems:
        return ShadowConsumerResult(
            status="OBSERVATION_BLOCKED",
            blockers=tuple(problems),
            command_id=item.command_id,
        )
    try:
        observed = record_observation(
            db,
            command_id=item.command_id,
            observer_generation=generation,
            observer_release_sha=sha,
            now=current,
            max_age_seconds=max_age_seconds,
        )
    except DesktopRequestError as exc:
        return ShadowConsumerResult(
            status="OBSERVATION_FENCED",
            blockers=(str(exc),),
            command_id=item.command_id,
        )
    return ShadowConsumerResult(
        status=observed.state,
        blockers=(),
        command_id=item.command_id,
        observation_recorded=True,
    )
