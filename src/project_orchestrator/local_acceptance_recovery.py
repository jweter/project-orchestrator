"""IG-034: read-only classification of interrupted Git/SQLite acceptance.

Classification never grants acceptance or changes refs. A controller must
revalidate native preflight, Product Reality and owner authority before repair.
"""

from __future__ import annotations

from dataclasses import dataclass

from project_orchestrator.human_ownership import AUTHORIZED, owner_control_status
from project_orchestrator.local_first_state import LocalFirstState
from project_orchestrator.local_git_refs import LocalGitRefs


@dataclass(frozen=True)
class AcceptanceRecovery:
    milestone_id: str
    disposition: str
    accepted_sha: str


def classify_pending_acceptances(
    *, state: LocalFirstState, refs: LocalGitRefs
) -> list[AcceptanceRecovery]:
    """Inspect durable intents without promoting or finalizing anything."""
    if owner_control_status(refs.owner_state_root) != AUTHORIZED:
        raise ValueError("OWNER_CONTROL_VETO")
    accepted = refs.accepted()
    outcomes = []
    for intent in state.pending_acceptances():
        milestone = intent["milestone_id"]
        target = intent["accepted_sha"]
        base = intent["accepted_base_sha"]
        anchor = refs.get("milestone", milestone)
        if anchor is not None and anchor != target:
            disposition = "MILESTONE_REF_CONFLICT"
        elif accepted is None:
            disposition = "ACCEPTED_REF_MISSING_STOP"
        elif accepted == target and anchor == target:
            disposition = "CAS_DONE_REVALIDATE_BEFORE_FINALIZE"
        elif accepted == target:
            disposition = "MILESTONE_REF_MISSING_STOP"
        elif accepted == base and anchor == target:
            disposition = "CAS_NOT_DONE_REVALIDATE_BEFORE_ADVANCE"
        elif accepted == base:
            disposition = "MILESTONE_REF_MISSING_STOP"
        else:
            disposition = "ACCEPTED_REF_DIVERGED_STOP"
        outcomes.append(AcceptanceRecovery(milestone, disposition, target))
    if owner_control_status(refs.owner_state_root) != AUTHORIZED:
        raise ValueError("OWNER_CONTROL_VETO")
    return outcomes
