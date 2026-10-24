"""Rocksmith candidate explanation trace (roadmap section 24:
Rocksmith Slice Selector; Phase 7: Event-Driven Scheduler, final
deliverable).

iron_god_conflict_aware_scheduling.py (IG-073) already covers steps
5-6 of the roadmap's seven-step selection algorithm (compare conflict
footprint, choose highest-value conflict-free slice). This module
adds the earlier disqualifying steps ahead of it, in the roadmap's
own order -- step 2 (already-owned), step 3 (dependency-blocked, via
iron_god_ambiguity_detector.py's IG-065 ambiguity check), step 4
(resource-incompatible) -- and reports the FIRST applicable
disqualifying reason for every candidate, never a bare rejection with
no explanation, per the roadmap: "If no slice can run, the decision
record must say exactly why every candidate was rejected.
NO_CONFLICT_FREE_READY_SLICE without per-candidate explanation is
insufficient." Step 7 ("claim it atomically") remains the caller's
own mutation, out of scope here. This module performs no file or
state mutation.
"""

from __future__ import annotations

from dataclasses import dataclass

from .iron_god_ambiguity_detector import AMBIGUOUS, AmbiguityCheck
from .iron_god_conflict_aware_scheduling import SELECTED, SchedulingDecision

RECOMMENDED = "RECOMMENDED"
REJECTED_OWNED = "REJECTED_OWNED"
REJECTED_DEPENDENCY_BLOCKED = "REJECTED_DEPENDENCY_BLOCKED"
REJECTED_RESOURCE_INCOMPATIBLE = "REJECTED_RESOURCE_INCOMPATIBLE"
REJECTED_SCHEDULING_UNVERIFIED = "REJECTED_SCHEDULING_UNVERIFIED"


@dataclass(frozen=True)
class CandidateExplanation:
    slice_id: str
    status: str
    reason: str


@dataclass(frozen=True)
class CandidateInput:
    slice_id: str
    is_owned: bool
    ambiguity_check: AmbiguityCheck | None
    resource_compatible: bool
    scheduling_decision: SchedulingDecision | None


def explain_candidate(
    slice_id: str,
    *,
    is_owned: bool,
    ambiguity_check: AmbiguityCheck | None,
    resource_compatible: bool,
    scheduling_decision: SchedulingDecision | None,
) -> CandidateExplanation:
    """Decide the first applicable disqualifying reason for
    `slice_id`, in the roadmap's own selector step order, or
    RECOMMENDED if none applies."""
    if is_owned:
        return CandidateExplanation(
            slice_id=slice_id,
            status=REJECTED_OWNED,
            reason="slice is already owned by an active worker",
        )

    if ambiguity_check is not None and ambiguity_check.status == AMBIGUOUS:
        return CandidateExplanation(
            slice_id=slice_id,
            status=REJECTED_DEPENDENCY_BLOCKED,
            reason=f"dependency-blocked: {ambiguity_check.reason}",
        )

    if not resource_compatible:
        return CandidateExplanation(
            slice_id=slice_id,
            status=REJECTED_RESOURCE_INCOMPATIBLE,
            reason="declared resource/runner requirements are not currently satisfiable",
        )

    if scheduling_decision is not None and scheduling_decision.status != SELECTED:
        return CandidateExplanation(
            slice_id=slice_id,
            status=scheduling_decision.status,
            reason=scheduling_decision.reason,
        )

    return CandidateExplanation(
        slice_id=slice_id,
        status=RECOMMENDED,
        reason="passed ownership, dependency, resource, and conflict/capacity checks",
    )


def explain_candidates(
    candidates: tuple[CandidateInput, ...],
) -> tuple[CandidateExplanation, ...]:
    """Explain every candidate in `candidates`, in order. Every
    candidate receives exactly one explanation -- there is no
    aggregate outcome that omits a candidate's own reason."""
    return tuple(
        explain_candidate(
            candidate.slice_id,
            is_owned=candidate.is_owned,
            ambiguity_check=candidate.ambiguity_check,
            resource_compatible=candidate.resource_compatible,
            scheduling_decision=candidate.scheduling_decision,
        )
        for candidate in candidates
    )
