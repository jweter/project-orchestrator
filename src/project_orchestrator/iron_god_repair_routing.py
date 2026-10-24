"""Failed-integration repair routing.

Given failure evidence and a prior-attempt count for that failure's
signature, decides whether to queue a new bounded repair attempt or
quarantine for root-cause review. Mirrors the signature/budget policy
already proven in local_continuous_worker.py's
record_failure_and_queue_repair, as a pure, storage-independent
classification function.

This module performs no Git operation, provisions no worktree, and
performs no actual repair -- it only decides, and the candidate itself
is never discarded or deleted by anything here.
"""

from __future__ import annotations

from dataclasses import dataclass

KNOWN_FAILURE_KINDS = frozenset(
    {"NON_GREEN_RECEIPT", "INTEGRATION_CONFLICT", "AGENT_NO_PROGRESS"}
)

REPAIR_QUEUED = "REPAIR_QUEUED"
QUARANTINED = "QUARANTINED"


@dataclass(frozen=True)
class FailureEvidence:
    parent_slice_id: str
    failure_kind: str
    candidate_sha: str
    base_sha: str
    detail: str


@dataclass(frozen=True)
class RepairDecision:
    parent_slice_id: str
    status: str
    repair_slice_id: str | None
    attempt_number: int
    reason: str


def classify_failure(evidence: FailureEvidence) -> str:
    """Validate and return the evidence's failure kind. Raises on any
    kind outside the known set -- an unrecognized failure is never
    silently routed as if it were understood."""
    if evidence.failure_kind not in KNOWN_FAILURE_KINDS:
        raise ValueError(f"unknown failure_kind: {evidence.failure_kind!r}")
    return evidence.failure_kind


def route_repair(
    evidence: FailureEvidence,
    *,
    prior_attempts_for_signature: int,
    max_same_signature_attempts: int = 3,
) -> RepairDecision:
    """Decide whether this failure becomes a new bounded repair attempt
    or is quarantined for recurring the same way too many times.

    The boundary attempt (attempt_number == max_same_signature_attempts)
    still queues; only exceeding the budget quarantines.
    """
    classify_failure(evidence)
    attempt_number = prior_attempts_for_signature + 1

    if attempt_number > max_same_signature_attempts:
        return RepairDecision(
            parent_slice_id=evidence.parent_slice_id,
            status=QUARANTINED,
            repair_slice_id=None,
            attempt_number=attempt_number,
            reason=(
                f"same failure signature recurred {prior_attempts_for_signature} "
                f"times (budget {max_same_signature_attempts}); quarantining for "
                "root-cause review"
            ),
        )

    repair_slice_id = f"{evidence.parent_slice_id}-R{attempt_number:02d}"
    return RepairDecision(
        parent_slice_id=evidence.parent_slice_id,
        status=REPAIR_QUEUED,
        repair_slice_id=repair_slice_id,
        attempt_number=attempt_number,
        reason=(
            f"{evidence.failure_kind} on candidate {evidence.candidate_sha}; "
            "queuing bounded repair"
        ),
    )
