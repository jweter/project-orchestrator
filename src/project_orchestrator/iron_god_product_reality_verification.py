"""Wires Product Reality evaluation into the local-acceptance boundary
(roadmap section 6; Phase 8's own framing: a Verification Receipt and a
Product Reality receipt are named as separate, parallel evidence in
the accepted-milestone evidence set -- not one wrapped inside the
other).

Confirmed by direct inspection of local_continuous_worker.py and
accepted_event_dispatch_coordinator.py (grep for any product_reality
reference returns nothing): Product Reality is not wired into the
live candidate pipeline today. A VERIFIED_CANDIDATE result from
execute_candidate_attempt means the code passed ruff/pytest/the
26-gate preflight -- nothing about Product Reality has been checked.

This module does not attempt to wrap or replace verify_and_commit as
an execute_candidate_attempt verifier_fn substitute. That seam was
checked directly: execute_candidate_attempt only threads
lease_heartbeat through to verifier_fn when it is identically the
module-global verify_and_commit object (`if verifier_fn is
verify_and_commit: verifier_fn(task, lease_heartbeat=renew_lease)
else: verifier_fn(task)`); any substituted wrapper would silently lose
lease-heartbeat renewal during a real, long-running verify. Instead,
this module is the gate a caller runs on a VERIFIED_CANDIDATE result's
own candidate_sha, before that commit is ever locally accepted as a
new milestone (the exact point proven in
tests/test_iron_god_complete_loop_proof.py's second-slice-continuation
test) -- exactly where the roadmap's "Product Reality required" check
belongs, and the only place that composes IG-080/IG-086 without
touching the live controller or losing lease heartbeating.

Composes (never duplicates): iron_god_product_reality_adapter.py
(IG-080)'s ProductRealityAdapter Protocol and PASS/FAIL/HUMAN_DEBT
precedence (ProductRealityEvidence.overall_status), and
iron_god_product_reality_receipt.py (IG-086)'s tamper-detecting
receipt builder. Performs no evaluation itself and no Git/SQLite
operation -- it only decides whether a candidate_sha may proceed to
local acceptance.
"""

from __future__ import annotations

from dataclasses import dataclass

from .iron_god_product_reality_adapter import (
    FAIL,
    HUMAN_DEBT,
    PASS,
    ProductRealityAdapter,
)
from .iron_god_product_reality_receipt import ProductRealityReceipt, build_receipt

NOT_REQUIRED = "NOT_REQUIRED"

_BLOCKING_STATUSES = frozenset({FAIL, HUMAN_DEBT})


@dataclass(frozen=True)
class ProductRealityGateDecision:
    status: str
    blocks_acceptance: bool
    receipt: ProductRealityReceipt | None
    reason: str


def evaluate_product_reality_for_candidate(
    adapter: ProductRealityAdapter | None,
    repository: str,
    candidate_sha: str,
    *,
    required: bool,
) -> ProductRealityGateDecision:
    """Decide whether `candidate_sha` may proceed to local acceptance.

    required=False never calls `adapter` and always returns
    NOT_REQUIRED, blocks_acceptance=False -- matching
    LocalFirstState.prepare_acceptance's own existing
    product_reality='NOT_REQUIRED' acceptance path.

    required=True calls adapter.evaluate(repository, candidate_sha) and
    builds a tamper-detecting receipt over its evidence. PASS never
    blocks. Both FAIL and HUMAN_DEBT block acceptance -- a subjective,
    not-yet-reviewed category is never treated as passing, matching
    IG-080's own ProductRealityEvidence.overall_status precedence
    (reused here, not re-derived): FAIL and HUMAN_DEBT both already
    take priority over PASS there, and this gate makes both
    load-bearing for acceptance, not just for the receipt's own status
    field.

    required=True with adapter=None raises -- a repository that
    requires Product Reality but has no real adapter wired must never
    silently be treated as passing.
    """
    if not required:
        return ProductRealityGateDecision(
            status=NOT_REQUIRED,
            blocks_acceptance=False,
            receipt=None,
            reason="Product Reality not required for this repository/milestone",
        )
    if adapter is None:
        raise ValueError(
            "Product Reality is required but no adapter was supplied; "
            "refusing to silently treat this candidate as passing"
        )

    evidence = adapter.evaluate(repository, candidate_sha)
    receipt = build_receipt(evidence)
    status = receipt.evidence.overall_status

    if status == PASS:
        return ProductRealityGateDecision(
            status=PASS,
            blocks_acceptance=False,
            receipt=receipt,
            reason="every Product Reality category explicitly passed",
        )
    if status in _BLOCKING_STATUSES:
        reason = (
            "at least one Product Reality category failed"
            if status == FAIL
            else (
                "at least one Product Reality category is HUMAN_DEBT "
                "(a subjective evaluation requiring human sign-off) and "
                "has not been resolved"
            )
        )
        return ProductRealityGateDecision(
            status=status,
            blocks_acceptance=True,
            receipt=receipt,
            reason=reason,
        )
    raise ValueError(f"unrecognized Product Reality overall_status: {status!r}")
