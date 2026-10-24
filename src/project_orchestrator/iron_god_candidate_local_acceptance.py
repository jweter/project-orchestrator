"""Automatic continuation: composes the Product Reality gate
(iron_god_product_reality_verification.py, IG this-session) with the
real local-acceptance mechanism (LocalFirstState + LocalGitRefs)
proven in tests/test_iron_god_complete_loop_proof.py, into one
auditable step a caller runs on a VERIFIED_CANDIDATE result before
the next slice can be dispatched.

This is the composition the Phase 4 loop proof found missing: a
VERIFIED_CANDIDATE result does not, by itself, create the new
acceptance event the next slice's dispatch needs (claims table
PRIMARY KEY is (source_db, acceptance_event_id) in
local_dispatch_event_claim.py, so a genuinely new acceptance event is
required for every slice after the first). Performing that acceptance
is a correctly, deliberately separate step today -- the real
candidate_outcome_reconciliation ledger entry notes
accepted_ref_authority: false, meaning this candidate pipeline does
not have authority to advance the accepted ref on its own. This module
does not change that authority boundary; it is a decision function a
caller (human-reviewed today, an eventual orchestration loop later,
once explicitly authorized) can call to perform the step correctly,
atomically, and with the Product Reality gate always checked first --
never the other way around.

Never calls iron_god_acceptance.accept_candidate -- that module was
confirmed, by reading it, to operate on a different, disconnected
ref/state system (iron_god_refs.py's repository-parametrized Git refs,
plus no LocalFirstState write at all) that the real dispatch selector
(local_acceptance_refill.select_next_after_acceptance) does not read.
"""

from __future__ import annotations

from dataclasses import dataclass

from .iron_god_product_reality_adapter import PASS as PR_PASS
from .iron_god_product_reality_adapter import ProductRealityAdapter
from .iron_god_product_reality_verification import (
    NOT_REQUIRED,
    ProductRealityGateDecision,
    evaluate_product_reality_for_candidate,
)
from .local_first_state import LocalFirstState
from .local_git_refs import LocalGitRefs

ACCEPTED = "ACCEPTED"
NOT_ACCEPTED = "NOT_ACCEPTED"

# LocalFirstState.prepare_acceptance only accepts product_reality in
# ("PASSED", "NOT_REQUIRED") -- a blocking gate result never reaches
# this far, so there is deliberately no entry here for FAIL/HUMAN_DEBT.
_GATE_STATUS_TO_LOCAL_FIRST_STATE_FIELD = {
    PR_PASS: "PASSED",
    NOT_REQUIRED: "NOT_REQUIRED",
}


class CandidateAcceptanceError(RuntimeError):
    """Raised when a non-blocking Product Reality result cannot be
    mapped to a valid LocalFirstState product_reality field -- this
    should be unreachable given evaluate_product_reality_for_candidate's
    own contract, and is checked explicitly rather than assumed."""


@dataclass(frozen=True)
class CandidateAcceptanceOutcome:
    status: str
    milestone_id: str | None
    accepted_sha: str | None
    product_reality: ProductRealityGateDecision
    reason: str


def accept_verified_candidate_as_next_milestone(
    *,
    state: LocalFirstState,
    refs: LocalGitRefs,
    repository: str,
    base_sha: str,
    candidate_sha: str,
    new_milestone_id: str,
    verification_sha256: str,
    product_reality_adapter: ProductRealityAdapter | None = None,
    product_reality_required: bool,
    context_json: str = "{}",
) -> CandidateAcceptanceOutcome:
    """Gate, then perform, local acceptance of a VERIFIED_CANDIDATE's
    own commit as the next milestone.

    Checks Product Reality FIRST. If it blocks acceptance (FAIL or an
    unresolved HUMAN_DEBT category), returns NOT_ACCEPTED and advances
    nothing -- no Git ref, no SQLite row, no partial state. Only on a
    non-blocking result (PASS, or NOT_REQUIRED) does this advance
    refs.accepted(), protect the new milestone ref, and durably commit
    the acceptance_intents/milestones/journal rows via the real
    two-phase LocalFirstState commit -- the identical sequence proven
    against a disposable repo in test_iron_god_complete_loop_proof.py.

    If refs.advance_accepted/protect succeeds but the subsequent
    LocalFirstState commit then raises, the accepted Git ref has moved
    but no matching SQLite milestone/intent row exists yet -- the same
    partial-failure shape iron_god_acceptance.accept_candidate's own
    docstring already documents for its own (different) ref system.
    This function does not invent new rollback machinery for that case;
    it propagates the exception, and a caller must re-inspect
    refs.accepted()/state.snapshot() before retrying rather than assume
    nothing happened.
    """
    decision = evaluate_product_reality_for_candidate(
        product_reality_adapter,
        repository,
        candidate_sha,
        required=product_reality_required,
    )
    if decision.blocks_acceptance:
        return CandidateAcceptanceOutcome(
            status=NOT_ACCEPTED,
            milestone_id=None,
            accepted_sha=None,
            product_reality=decision,
            reason=decision.reason,
        )

    product_reality_field = _GATE_STATUS_TO_LOCAL_FIRST_STATE_FIELD.get(decision.status)
    if product_reality_field is None:
        raise CandidateAcceptanceError(
            f"unexpected non-blocking Product Reality status: {decision.status!r}"
        )

    refs.advance_accepted(expected_sha=base_sha, new_sha=candidate_sha)
    refs.protect("milestone", new_milestone_id, candidate_sha)
    state.prepare_acceptance(
        repository=repository,
        milestone_id=new_milestone_id,
        accepted_base_sha=base_sha,
        accepted_sha=candidate_sha,
        verification_sha256=verification_sha256,
        product_reality=product_reality_field,
        context_json=context_json,
    )
    state.finish_prepared_acceptance(
        milestone_id=new_milestone_id,
        expected_sha=candidate_sha,
        expected_verification_sha256=verification_sha256,
    )
    return CandidateAcceptanceOutcome(
        status=ACCEPTED,
        milestone_id=new_milestone_id,
        accepted_sha=candidate_sha,
        product_reality=decision,
        reason="candidate accepted as new milestone",
    )
