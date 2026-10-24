from __future__ import annotations

import pytest

from project_orchestrator.iron_god_repair_routing import (
    QUARANTINED,
    REPAIR_QUEUED,
    FailureEvidence,
    classify_failure,
    route_repair,
)


def _evidence(**overrides) -> FailureEvidence:
    defaults = dict(
        parent_slice_id="IG-100",
        failure_kind="NON_GREEN_RECEIPT",
        candidate_sha="a" * 40,
        base_sha="b" * 40,
        detail="lint gate failed",
    )
    defaults.update(overrides)
    return FailureEvidence(**defaults)


def test_classify_failure_rejects_unknown_kind():
    with pytest.raises(ValueError):
        classify_failure(_evidence(failure_kind="SOMETHING_ELSE"))


@pytest.mark.parametrize(
    "kind", ["NON_GREEN_RECEIPT", "INTEGRATION_CONFLICT", "AGENT_NO_PROGRESS"]
)
def test_classify_failure_accepts_known_kinds(kind):
    assert classify_failure(_evidence(failure_kind=kind)) == kind


def test_route_repair_queues_first_attempt():
    decision = route_repair(_evidence(), prior_attempts_for_signature=0)
    assert decision.status == REPAIR_QUEUED
    assert decision.repair_slice_id == "IG-100-R01"
    assert decision.attempt_number == 1


def test_route_repair_queues_at_exact_budget_boundary():
    decision = route_repair(
        _evidence(),
        prior_attempts_for_signature=2,
        max_same_signature_attempts=3,
    )
    assert decision.status == REPAIR_QUEUED
    assert decision.repair_slice_id == "IG-100-R03"
    assert decision.attempt_number == 3


def test_route_repair_quarantines_once_budget_exceeded():
    decision = route_repair(
        _evidence(),
        prior_attempts_for_signature=3,
        max_same_signature_attempts=3,
    )
    assert decision.status == QUARANTINED
    assert decision.repair_slice_id is None
    assert decision.attempt_number == 4


def test_route_repair_validates_evidence_before_deciding():
    with pytest.raises(ValueError):
        route_repair(
            _evidence(failure_kind="BOGUS"),
            prior_attempts_for_signature=0,
        )


def test_route_repair_reason_mentions_failure_kind_and_candidate():
    decision = route_repair(
        _evidence(failure_kind="INTEGRATION_CONFLICT", candidate_sha="c" * 40),
        prior_attempts_for_signature=0,
    )
    assert "INTEGRATION_CONFLICT" in decision.reason
    assert "c" * 40 in decision.reason
