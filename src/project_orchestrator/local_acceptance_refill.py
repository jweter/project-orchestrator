"""Owner-fenced, read-only acceptance-to-next-slice handoff.

IG local-first migration: consumes the EXISTING committed SQLite acceptance
event plus protected Git refs. It does not accept commits, create worktrees,
claim leases, activate candidate mode, or start builders. Its output is only a
bounded, uniquely identified proposal for the existing leased dispatcher.
A later controller integration must atomically claim the event and slice.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .human_ownership import AUTHORIZED, owner_control_status
from .local_first_state import LocalFirstState
from .local_git_refs import LocalGitRefs


@dataclass(frozen=True)
class RefillDecision:
    status: str
    reason: str
    acceptance_event_id: int | None = None
    accepted_sha: str | None = None
    next_slice_id: str | None = None


def select_next_after_acceptance(
    *,
    state: LocalFirstState,
    refs: LocalGitRefs,
    owner_state_root: Path,
    milestone_id: str,
    controller: Mapping[str, Any],
    handoffs: Mapping[str, Mapping[str, Any]],
    active_slice_ids: Sequence[str] = (),
) -> RefillDecision:
    """One proposal after a durably COMMITTED acceptance, never a dispatch.

    The controller ledger is the sole source of runnable slice IDs; a handoff
    file cannot promote itself. All dependencies must name committed
    milestone IDs, not merely completed code or agent claims. The accepted
    SHA is required as the next candidate's exact base.
    """

    def held(reason: str) -> RefillDecision:
        return RefillDecision("HELD", reason)

    if owner_control_status(owner_state_root) != AUTHORIZED:
        return held("OWNER_CONTROL_VETO")
    if refs.owner_state_root.resolve() != Path(owner_state_root).resolve():
        return held("OWNER_AUTHORITY_ROOT_MISMATCH")
    if (
        controller.get("mode") != "candidate_mode"
        or controller.get("candidate_execution_authorized") is not True
    ):
        return held("CANDIDATE_EXECUTION_NOT_AUTHORIZED")

    snapshot = state.snapshot()
    intents = [
        row
        for row in snapshot["acceptance_intents"]
        if row["milestone_id"] == milestone_id
    ]
    if len(intents) != 1 or intents[0]["state"] != "COMMITTED":
        return held("ACCEPTANCE_NOT_DURABLY_COMMITTED")
    intent = intents[0]
    sha = intent["accepted_sha"]
    milestones = {row["milestone_id"]: row for row in snapshot["milestones"]}
    milestone = milestones.get(milestone_id)
    if (
        milestone is None
        or milestone["accepted_sha"] != sha
        or milestone["verification_sha256"] != intent["verification_sha256"]
        or milestone["repository"] != intent["repository"]
    ):
        return held("ACCEPTED_MILESTONE_RECORD_MISMATCH")
    events = [
        item
        for item in snapshot["journal"]
        if item["milestone_id"] == milestone_id
        and item["event_type"] == "MILESTONE_ACCEPTED"
        and item["detail"] == sha
    ]
    if len(events) != 1:
        return held("ACCEPTANCE_EVENT_UNVERIFIED")

    # An older milestone does not authorize scheduling from a stale baseline.
    if refs.accepted() != sha or refs.get("milestone", milestone_id) != sha:
        return held("PROTECTED_ACCEPTED_REFS_NOT_AT_TARGET")
    if any(
        item["repository"] == intent["repository"]
        for item in state.pending_acceptances()
    ):
        return held("REPOSITORY_ACCEPTANCE_STILL_PENDING")

    accepted_milestones = frozenset(
        m["milestone_id"]
        for m in snapshot["milestones"]
        if m["repository"] == intent["repository"]
    )
    active = set(active_slice_ids)
    rows = controller.get("slices")
    if not isinstance(rows, list):
        return held("CONTROLLER_LEDGER_UNVERIFIED")

    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            return held("CONTROLLER_ROW_MALFORMED")
        sid = row["id"]
        if sid in seen:
            return held("DUPLICATE_CONTROLLER_SLICE")
        seen.add(sid)
    for row in rows:
        sid = row["id"]
        if row.get("state") != "ELIGIBLE" or sid in active:
            continue
        packet = handoffs.get(sid)
        if not isinstance(packet, Mapping):
            continue
        if packet.get("slice_id") != sid or packet.get("state") != "ELIGIBLE":
            continue
        if packet.get("repository") != intent["repository"]:
            continue
        if packet.get("exact_base_sha") != sha:
            continue
        required = packet.get("requires_milestones")
        if (
            not isinstance(required, list)
            or any(not isinstance(dep, str) for dep in required)
            or not set(required).issubset(accepted_milestones)
        ):
            continue
        if not packet.get("worktree_name") or not packet.get("branch"):
            continue
        # Recheck the owner and protected refs just before publishing advice.
        if owner_control_status(owner_state_root) != AUTHORIZED:
            return held("OWNER_CONTROL_REVOKED")
        if refs.accepted() != sha or refs.get("milestone", milestone_id) != sha:
            return held("PROTECTED_ACCEPTED_REFS_CHANGED")
        return RefillDecision(
            status="READY_FOR_LEASED_DISPATCH",
            reason="DURABLE_ACCEPTANCE_AND_AUTHORIZED_READY_SLICE",
            acceptance_event_id=events[0]["event_id"],
            accepted_sha=sha,
            next_slice_id=sid,
        )
    return RefillDecision("IDLE", "NO_DEPENDENCY_READY_SLICE")
