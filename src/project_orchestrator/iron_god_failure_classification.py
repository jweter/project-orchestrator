"""Failure classification across the development pipeline (JER-37).

The existing repair router (iron_god_repair_routing.py) only ever sees
three failure kinds -- NON_GREEN_RECEIPT, INTEGRATION_CONFLICT,
AGENT_NO_PROGRESS -- and all three are genuine engineering failures
that are correctly eligible for a bounded code-repair loop. Nothing
upstream of it distinguishes those from a tool/platform permission
denial, an infrastructure failure, resource contention, a publication
transport failure, or an owner-control restriction. Left unclassified,
any of those could be handed to the repair router as if it were an
ordinary engineering bug -- which would be wrong in a safety-relevant
way for a permission denial or an owner-control restriction: it must
never be "repaired" (there is no code defect to fix) and must never
cause an alternative execution route to be attempted (that would be
exactly the kind of denial-circumvention this project's standing
safety constraints forbid). This module classifies a raw failure
signal into exactly one category first, and only ever delegates to the
real route_repair() when that category is GENUINE_ENGINEERING_FAILURE.

The signal -> category mapping is deliberately closed: an unrecognized
signal raises FailureClassificationError rather than being guessed
into a category. Defaulting an unknown signal to "genuine engineering
failure" would be the one default that could silently let a real
permission denial or owner-control restriction slip into the repair
loop, so there is no default at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from project_orchestrator.iron_god_repair_routing import (
    FailureEvidence,
    RepairDecision,
    route_repair,
)

CATEGORY_GENUINE_ENGINEERING_FAILURE = "GENUINE_ENGINEERING_FAILURE"
CATEGORY_TOOL_PERMISSION_DENIAL = "TOOL_PERMISSION_DENIAL"
CATEGORY_INFRASTRUCTURE_FAILURE = "INFRASTRUCTURE_FAILURE"
CATEGORY_RESOURCE_CONTENTION = "RESOURCE_CONTENTION"
CATEGORY_PUBLICATION_TRANSPORT_FAILURE = "PUBLICATION_TRANSPORT_FAILURE"
CATEGORY_OWNER_CONTROL_RESTRICTION = "OWNER_CONTROL_RESTRICTION"

KNOWN_CATEGORIES = frozenset(
    {
        CATEGORY_GENUINE_ENGINEERING_FAILURE,
        CATEGORY_TOOL_PERMISSION_DENIAL,
        CATEGORY_INFRASTRUCTURE_FAILURE,
        CATEGORY_RESOURCE_CONTENTION,
        CATEGORY_PUBLICATION_TRANSPORT_FAILURE,
        CATEGORY_OWNER_CONTROL_RESTRICTION,
    }
)

ACTION_BOUNDED_REPAIR = "BOUNDED_REPAIR"
ACTION_BOUNDED_RETRY_LATER = "BOUNDED_RETRY_LATER"
ACTION_DEFER_TO_TRANSPORT = "DEFER_TO_TRANSPORT"
ACTION_HALT_REQUIRES_OWNER = "HALT_REQUIRES_OWNER"

_CATEGORY_ACTION = {
    CATEGORY_GENUINE_ENGINEERING_FAILURE: ACTION_BOUNDED_REPAIR,
    CATEGORY_INFRASTRUCTURE_FAILURE: ACTION_BOUNDED_RETRY_LATER,
    CATEGORY_RESOURCE_CONTENTION: ACTION_BOUNDED_RETRY_LATER,
    CATEGORY_PUBLICATION_TRANSPORT_FAILURE: ACTION_DEFER_TO_TRANSPORT,
    CATEGORY_TOOL_PERMISSION_DENIAL: ACTION_HALT_REQUIRES_OWNER,
    CATEGORY_OWNER_CONTROL_RESTRICTION: ACTION_HALT_REQUIRES_OWNER,
}

# Closed signal -> category mapping.
#
# The GENUINE_ENGINEERING_FAILURE entries are exactly
# iron_god_repair_routing.KNOWN_FAILURE_KINDS -- the only failure_kind
# values route_repair() actually accepts. This is deliberate: those
# three are the only signals in this codebase today that are recorded
# as FailureEvidence and fed through the bounded repair loop (see
# local_continuous_worker.py's record_failure_and_queue_repair). A
# worktree/dispatch validation rejection raised before any lease is
# ever claimed (UNEXPECTED_BRANCH, SLICE_BASE_SHA_MISMATCH,
# UNEXPECTED_WORKTREE_CHANGE, and similar) never reaches
# record_failure_and_queue_repair in the real pipeline, so it is
# deliberately NOT included here -- classifying it as
# GENUINE_ENGINEERING_FAILURE would make route_or_halt attempt to call
# route_repair() with a failure_kind that function does not accept.
#
# The tool/platform permission denial and owner-control entries are
# real categories observed in this project's own sessions. The
# infrastructure/resource/publication-transport entries are this
# module's own vocabulary for those categories (JER-37 introduces
# them; they are not literal strings raised elsewhere in this
# codebase today). OWNER_OR_RESOURCE_VETO is a single signal name the
# existing controller code uses for two different real causes (an
# owner-control veto or a resource-gate failure); it is conservatively
# classified as OWNER_CONTROL_RESTRICTION -- the more restrictive,
# halting interpretation -- since treating an actual owner veto as
# mere transient resource contention would risk an inappropriate
# automatic retry, whereas treating a resource shortage as "needs
# owner action" is only overly cautious, never unsafe.
_SIGNAL_TO_CATEGORY = {
    # genuine engineering failures -- eligible for the bounded repair loop
    "NON_GREEN_RECEIPT": CATEGORY_GENUINE_ENGINEERING_FAILURE,
    "INTEGRATION_CONFLICT": CATEGORY_GENUINE_ENGINEERING_FAILURE,
    "AGENT_NO_PROGRESS": CATEGORY_GENUINE_ENGINEERING_FAILURE,
    # tool/platform permission denials -- must halt, never repair or retry
    "SECURITY_WEAKEN_DENIED": CATEGORY_TOOL_PERMISSION_DENIAL,
    "MODIFY_SHARED_RESOURCES_DENIED": CATEGORY_TOOL_PERMISSION_DENIAL,
    "SELF_MODIFICATION_DENIED": CATEGORY_TOOL_PERMISSION_DENIAL,
    "PERMISSION_CLASSIFIER_DENIED": CATEGORY_TOOL_PERMISSION_DENIAL,
    # infrastructure failures -- not a code defect, not an engineering repair
    "GIT_EXECUTION_UNAVAILABLE": CATEGORY_INFRASTRUCTURE_FAILURE,
    "GIT_COMMAND_FAILED": CATEGORY_INFRASTRUCTURE_FAILURE,
    "DATABASE_INTEGRITY_FAILED": CATEGORY_INFRASTRUCTURE_FAILURE,
    "ACCEPTANCE_STATE_UNREADABLE": CATEGORY_INFRASTRUCTURE_FAILURE,
    # resource contention -- transient, bounded retry later, no code change
    "SLICE_LEASE_OCCUPIED": CATEGORY_RESOURCE_CONTENTION,
    "SLICE_LEASE_FENCE_REJECTED": CATEGORY_RESOURCE_CONTENTION,
    # publication transport failures -- routed to the dedicated transport
    # handling (iron_god_transport_deferral.py), never treated as a code defect
    "TRANSPORT_BLOCKED": CATEGORY_PUBLICATION_TRANSPORT_FAILURE,
    "CI_FAILED": CATEGORY_PUBLICATION_TRANSPORT_FAILURE,
    "REVIEW_CHANGES_REQUIRED": CATEGORY_PUBLICATION_TRANSPORT_FAILURE,
    "BASE_DIVERGED": CATEGORY_PUBLICATION_TRANSPORT_FAILURE,
    # owner-control restrictions -- must halt, never repair or retry
    "OWNER_CONTROL_VETO": CATEGORY_OWNER_CONTROL_RESTRICTION,
    "OWNER_OR_RESOURCE_VETO": CATEGORY_OWNER_CONTROL_RESTRICTION,
    "CANDIDATE_EXECUTION_NOT_AUTHORIZED": CATEGORY_OWNER_CONTROL_RESTRICTION,
    "CANDIDATE_MODE_NOT_EXPLICITLY_AUTHORIZED": CATEGORY_OWNER_CONTROL_RESTRICTION,
    "CANDIDATE_AUTHORIZATION_REVOKED": CATEGORY_OWNER_CONTROL_RESTRICTION,
    "OWNER_CONTROL_ACTIVE_OR_UNVERIFIED": CATEGORY_OWNER_CONTROL_RESTRICTION,
}


class FailureClassificationError(RuntimeError):
    """Raised for an unrecognized signal or category -- never guessed
    into GENUINE_ENGINEERING_FAILURE by default."""


@dataclass(frozen=True)
class ClassifiedFailure:
    signal: str
    category: str
    action: str
    may_enter_repair_loop: bool
    may_retry_automatically: bool
    requires_explicit_owner_action: bool


@dataclass(frozen=True)
class FailureRoutingResult:
    classified: ClassifiedFailure
    repair_decision: Optional[RepairDecision]


def classify_failure_signal(signal: str) -> str:
    """Classify a raw failure/denial signal string into one of the six
    known categories. Raises FailureClassificationError on anything
    unrecognized."""
    if signal not in _SIGNAL_TO_CATEGORY:
        raise FailureClassificationError(f"unrecognized failure signal: {signal!r}")
    return _SIGNAL_TO_CATEGORY[signal]


def decide_action(category: str) -> str:
    if category not in KNOWN_CATEGORIES:
        raise FailureClassificationError(f"unknown category: {category!r}")
    return _CATEGORY_ACTION[category]


def classify(signal: str) -> ClassifiedFailure:
    category = classify_failure_signal(signal)
    action = decide_action(category)
    return ClassifiedFailure(
        signal=signal,
        category=category,
        action=action,
        may_enter_repair_loop=action == ACTION_BOUNDED_REPAIR,
        may_retry_automatically=action
        in {ACTION_BOUNDED_REPAIR, ACTION_BOUNDED_RETRY_LATER},
        requires_explicit_owner_action=action == ACTION_HALT_REQUIRES_OWNER,
    )


def route_or_halt(
    signal: str,
    *,
    evidence: Optional[FailureEvidence] = None,
    prior_attempts_for_signature: int = 0,
    max_same_signature_attempts: int = 3,
) -> FailureRoutingResult:
    """Classify `signal`, then -- only when the classification is
    GENUINE_ENGINEERING_FAILURE -- delegate to the existing bounded
    repair router (iron_god_repair_routing.route_repair). Every other
    category returns with repair_decision=None: a tool/platform
    permission denial or owner-control restriction is a stop signal,
    never an input to the repair loop, and never implies trying an
    alternative execution route. `evidence` is required only for the
    GENUINE_ENGINEERING_FAILURE case; it is accepted but unused (and
    the repair router is never invoked) for every other category, so a
    caller cannot accidentally route a permission denial into repair
    by supplying evidence for it. When `evidence` is given, its
    `failure_kind` must equal `signal` -- a caller passing mismatched
    evidence for a different failure than the one just classified is
    a bug, not something to silently paper over.
    """
    classified = classify(signal)
    if classified.category != CATEGORY_GENUINE_ENGINEERING_FAILURE:
        return FailureRoutingResult(classified=classified, repair_decision=None)
    if evidence is None:
        raise FailureClassificationError(
            "GENUINE_ENGINEERING_FAILURE classification requires FailureEvidence "
            "to route a repair decision"
        )
    if evidence.failure_kind != signal:
        raise FailureClassificationError(
            f"evidence.failure_kind {evidence.failure_kind!r} does not match "
            f"classified signal {signal!r}"
        )
    repair_decision = route_repair(
        evidence,
        prior_attempts_for_signature=prior_attempts_for_signature,
        max_same_signature_attempts=max_same_signature_attempts,
    )
    return FailureRoutingResult(classified=classified, repair_decision=repair_decision)
