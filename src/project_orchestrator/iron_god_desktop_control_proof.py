"""Read-only evidence reconciliation for the two Iron God desktop controls.

This is NOT a control implementation, a source of owner authority, or permission
to start an agent. It never reads the retired owner marker, touches scheduled
tasks, writes controller state, or acknowledges a command. The existing desktop
scripts need a separately authorized cutover before they can consume this proof.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class DesktopResumeEvidence:
    status: str
    problems: tuple[str, ...]
    execution_authorized: bool = False


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _path(value: object) -> str:
    return (
        value.replace("/", "\\").rstrip("\\").casefold()
        if isinstance(value, str)
        else ""
    )


def _recent(value: object, *, now: datetime, max_age_seconds: int) -> bool:
    stamp = _timestamp(value)
    return stamp is not None and 0 <= (now - stamp).total_seconds() <= max_age_seconds


def diagnose_desktop_resume(
    *,
    request: Mapping[str, Any],
    acknowledgement: Mapping[str, Any],
    supervisor_pin: Mapping[str, Any],
    actor_pin: Mapping[str, Any],
    heartbeat: Mapping[str, Any],
    controller: Mapping[str, Any],
    supervisor_processes: Sequence[Mapping[str, Any]],
    actor_processes: Sequence[Mapping[str, Any]],
    owner_authority: str = "UNVERIFIED",
    now: datetime | None = None,
    max_age_seconds: int = 120,
) -> DesktopResumeEvidence:
    """Require command-specific acknowledgment and live generation consistency.

    Inputs must come from separately verified observations. Even when every
    predicate agrees, this function grants *no* owner or execution authority.
    """
    current = now or datetime.now(UTC)
    if current.tzinfo is None or max_age_seconds <= 0:
        raise ValueError("An aware clock and positive freshness horizon are required.")
    current = current.astimezone(UTC)
    problems: list[str] = []

    if owner_authority != "VERIFIED_RELEASED":
        problems.append("OWNER_CONTROL_UNVERIFIED")
    if (
        controller.get("mode") != "candidate_mode"
        or controller.get("candidate_execution_authorized") is not True
    ):
        problems.append("CONTROLLER_CANDIDATE_EXECUTION_DISABLED")

    sha = supervisor_pin.get("accepted_sha")
    if not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        problems.append("PIN_SHA_INVALID")
    if (
        supervisor_pin.get("enabled") is not True
        or actor_pin.get("enabled") is not True
    ):
        problems.append("RELEASE_PIN_DISABLED")
    if actor_pin.get("accepted_sha") != sha or _path(
        actor_pin.get("accepted_repo")
    ) != _path(supervisor_pin.get("accepted_repo")):
        problems.append("SUPERVISOR_ACTOR_PIN_MISMATCH")
    if not _path(supervisor_pin.get("accepted_repo")) or not _path(
        actor_pin.get("actor_repo")
    ):
        problems.append("PIN_ROOT_MISSING")

    request_id = request.get("command_id")
    if (
        not isinstance(request_id, str)
        or len(request_id) < 16
        or request.get("command") != "TAKE_THE_WHEEL"
    ):
        problems.append("COMMAND_REQUEST_IDENTITY_UNVERIFIED")
    if not _recent(
        request.get("requested_at_utc"), now=current, max_age_seconds=max_age_seconds
    ):
        problems.append("COMMAND_REQUEST_NOT_FRESH")

    if (
        acknowledgement.get("command_id") != request_id
        or acknowledgement.get("acknowledged_by_engine") is not True
        or acknowledgement.get("status") != "APPLIED"
    ):
        problems.append("SUPERVISOR_COMMAND_ACK_MISSING")
    if acknowledgement.get("accepted_sha") != sha:
        problems.append("ACK_RELEASE_MISMATCH")
    if not _recent(
        acknowledgement.get("acknowledged_at_utc"),
        now=current,
        max_age_seconds=max_age_seconds,
    ):
        problems.append("SUPERVISOR_ACK_NOT_FRESH")
    request_time = _timestamp(request.get("requested_at_utc"))
    ack_time = _timestamp(acknowledgement.get("acknowledged_at_utc"))
    if request_time is None or ack_time is None or ack_time < request_time:
        problems.append("ACK_PREDATES_REQUEST")

    generation = heartbeat.get("process_instance_id")
    if (
        not isinstance(generation, str)
        or not generation
        or heartbeat.get("status") != "RUNNING_CURRENT"
        or heartbeat.get("actor_revision") != sha
    ):
        problems.append("SUPERVISOR_GENERATION_UNVERIFIED")
    if not _recent(
        heartbeat.get("heartbeat_at_utc"), now=current, max_age_seconds=max_age_seconds
    ):
        problems.append("SUPERVISOR_HEARTBEAT_NOT_FRESH")
    if acknowledgement.get("process_instance_id") != generation:
        problems.append("ACK_GENERATION_MISMATCH")

    # These are observations supplied by the caller, not process discovery.
    # A PID alone, cached heartbeat, or historical desktop receipt is never proof.
    if len(supervisor_processes) != 1:
        problems.append("SUPERVISOR_PROCESS_COUNT_UNVERIFIED")
    else:
        proc = supervisor_processes[0]
        if (
            proc.get("process_instance_id") != generation
            or proc.get("accepted_sha") != sha
            or _path(proc.get("release_repo"))
            != _path(supervisor_pin.get("accepted_repo"))
            or not isinstance(proc.get("pid"), int)
            or proc["pid"] <= 0
        ):
            problems.append("SUPERVISOR_PROCESS_IDENTITY_MISMATCH")
    if len(actor_processes) != 1:
        problems.append("ACTOR_PROCESS_COUNT_UNVERIFIED")
    else:
        actor = actor_processes[0]
        if (
            actor.get("accepted_sha") != sha
            or _path(actor.get("actor_repo")) != _path(actor_pin.get("actor_repo"))
            or not isinstance(actor.get("pid"), int)
            or actor["pid"] <= 0
        ):
            problems.append("ACTOR_PROCESS_IDENTITY_MISMATCH")

    return DesktopResumeEvidence(
        status="BLOCKED" if problems else "EVIDENCE_ALIGNED_NOT_AUTHORIZATION",
        problems=tuple(problems),
        execution_authorized=False,
    )
