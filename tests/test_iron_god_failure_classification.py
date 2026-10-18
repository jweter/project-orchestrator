from __future__ import annotations

import pytest

from project_orchestrator.iron_god_failure_classification import (
    ACTION_BOUNDED_REPAIR,
    ACTION_BOUNDED_RETRY_LATER,
    ACTION_DEFER_TO_TRANSPORT,
    ACTION_HALT_REQUIRES_OWNER,
    CATEGORY_GENUINE_ENGINEERING_FAILURE,
    CATEGORY_INFRASTRUCTURE_FAILURE,
    CATEGORY_OWNER_CONTROL_RESTRICTION,
    CATEGORY_PUBLICATION_TRANSPORT_FAILURE,
    CATEGORY_RESOURCE_CONTENTION,
    CATEGORY_TOOL_PERMISSION_DENIAL,
    FailureClassificationError,
    classify,
    classify_failure_signal,
    decide_action,
    route_or_halt,
)
from project_orchestrator.iron_god_repair_routing import (
    QUARANTINED,
    REPAIR_QUEUED,
    FailureEvidence,
)


def test_genuine_engineering_signal_classifies_and_may_repair():
    result = classify("NON_GREEN_RECEIPT")
    assert result.category == CATEGORY_GENUINE_ENGINEERING_FAILURE
    assert result.action == ACTION_BOUNDED_REPAIR
    assert result.may_enter_repair_loop is True
    assert result.may_retry_automatically is True
    assert result.requires_explicit_owner_action is False


@pytest.mark.parametrize(
    "signal",
    [
        "SECURITY_WEAKEN_DENIED",
        "MODIFY_SHARED_RESOURCES_DENIED",
        "SELF_MODIFICATION_DENIED",
    ],
)
def test_tool_permission_denial_classifies_as_halt(signal):
    result = classify(signal)
    assert result.category == CATEGORY_TOOL_PERMISSION_DENIAL
    assert result.action == ACTION_HALT_REQUIRES_OWNER
    assert result.may_enter_repair_loop is False
    assert result.may_retry_automatically is False
    assert result.requires_explicit_owner_action is True


@pytest.mark.parametrize(
    "signal",
    [
        "OWNER_CONTROL_VETO",
        "OWNER_OR_RESOURCE_VETO",
        "CANDIDATE_EXECUTION_NOT_AUTHORIZED",
        "CANDIDATE_MODE_NOT_EXPLICITLY_AUTHORIZED",
    ],
)
def test_owner_control_restriction_classifies_as_halt(signal):
    result = classify(signal)
    assert result.category == CATEGORY_OWNER_CONTROL_RESTRICTION
    assert result.action == ACTION_HALT_REQUIRES_OWNER
    assert result.may_enter_repair_loop is False
    assert result.requires_explicit_owner_action is True


@pytest.mark.parametrize(
    "signal", ["GIT_EXECUTION_UNAVAILABLE", "DATABASE_INTEGRITY_FAILED"]
)
def test_infrastructure_failure_classifies_as_retry_later(signal):
    result = classify(signal)
    assert result.category == CATEGORY_INFRASTRUCTURE_FAILURE
    assert result.action == ACTION_BOUNDED_RETRY_LATER
    assert result.may_enter_repair_loop is False
    assert result.may_retry_automatically is True
    assert result.requires_explicit_owner_action is False


def test_resource_contention_classifies_as_retry_later():
    result = classify("SLICE_LEASE_OCCUPIED")
    assert result.category == CATEGORY_RESOURCE_CONTENTION
    assert result.action == ACTION_BOUNDED_RETRY_LATER
    assert result.may_enter_repair_loop is False
    assert result.may_retry_automatically is True


@pytest.mark.parametrize("signal", ["TRANSPORT_BLOCKED", "CI_FAILED", "BASE_DIVERGED"])
def test_publication_transport_failure_classifies_as_defer(signal):
    result = classify(signal)
    assert result.category == CATEGORY_PUBLICATION_TRANSPORT_FAILURE
    assert result.action == ACTION_DEFER_TO_TRANSPORT
    assert result.may_enter_repair_loop is False
    assert result.requires_explicit_owner_action is False


def test_unrecognized_signal_raises_never_defaults_to_engineering_failure():
    with pytest.raises(FailureClassificationError):
        classify("SOME_NEW_SIGNAL_NOBODY_HAS_SEEN_YET")


def test_classify_failure_signal_and_decide_action_are_independently_callable():
    category = classify_failure_signal("AGENT_NO_PROGRESS")
    assert category == CATEGORY_GENUINE_ENGINEERING_FAILURE
    assert decide_action(category) == ACTION_BOUNDED_REPAIR


def test_decide_action_rejects_unknown_category():
    with pytest.raises(FailureClassificationError):
        decide_action("NOT_A_REAL_CATEGORY")


def test_route_or_halt_genuine_engineering_failure_delegates_to_real_route_repair():
    evidence = FailureEvidence(
        parent_slice_id="IG-100",
        failure_kind="NON_GREEN_RECEIPT",
        candidate_sha="c" * 40,
        base_sha="b" * 40,
        detail="ruff check failed",
    )
    result = route_or_halt(
        "NON_GREEN_RECEIPT",
        evidence=evidence,
        prior_attempts_for_signature=0,
        max_same_signature_attempts=3,
    )
    assert result.classified.category == CATEGORY_GENUINE_ENGINEERING_FAILURE
    assert result.repair_decision is not None
    assert result.repair_decision.status == REPAIR_QUEUED
    assert result.repair_decision.repair_slice_id == "IG-100-R01"


def test_route_or_halt_genuine_engineering_failure_over_budget_quarantines():
    evidence = FailureEvidence(
        parent_slice_id="IG-100",
        failure_kind="AGENT_NO_PROGRESS",
        candidate_sha="c" * 40,
        base_sha="b" * 40,
        detail="candidate sha equals base sha",
    )
    result = route_or_halt(
        "AGENT_NO_PROGRESS",
        evidence=evidence,
        prior_attempts_for_signature=3,
        max_same_signature_attempts=3,
    )
    assert result.repair_decision.status == QUARANTINED


def test_route_or_halt_genuine_engineering_failure_without_evidence_raises():
    with pytest.raises(FailureClassificationError):
        route_or_halt("NON_GREEN_RECEIPT")


def test_route_or_halt_mismatched_evidence_failure_kind_raises():
    evidence = FailureEvidence(
        parent_slice_id="IG-100",
        failure_kind="INTEGRATION_CONFLICT",
        candidate_sha="c" * 40,
        base_sha="b" * 40,
        detail="wrong evidence for this signal",
    )
    with pytest.raises(FailureClassificationError):
        route_or_halt(
            "NON_GREEN_RECEIPT", evidence=evidence, prior_attempts_for_signature=0
        )


def test_route_or_halt_permission_denial_never_calls_route_repair_even_with_evidence():
    evidence = FailureEvidence(
        parent_slice_id="IG-100",
        failure_kind="SECURITY_WEAKEN_DENIED",
        candidate_sha="c" * 40,
        base_sha="b" * 40,
        detail="attempted to weaken a permission",
    )
    result = route_or_halt(
        "SECURITY_WEAKEN_DENIED",
        evidence=evidence,
        prior_attempts_for_signature=0,
    )
    assert result.classified.category == CATEGORY_TOOL_PERMISSION_DENIAL
    assert result.classified.requires_explicit_owner_action is True
    assert result.repair_decision is None


def test_route_or_halt_owner_control_restriction_never_calls_route_repair():
    result = route_or_halt("OWNER_CONTROL_VETO")
    assert result.classified.category == CATEGORY_OWNER_CONTROL_RESTRICTION
    assert result.classified.requires_explicit_owner_action is True
    assert result.repair_decision is None


def test_route_or_halt_resource_contention_never_calls_route_repair():
    result = route_or_halt("SLICE_LEASE_OCCUPIED")
    assert result.classified.category == CATEGORY_RESOURCE_CONTENTION
    assert result.classified.may_retry_automatically is True
    assert result.repair_decision is None


def test_route_or_halt_publication_transport_failure_never_calls_route_repair():
    result = route_or_halt("TRANSPORT_BLOCKED")
    assert result.classified.category == CATEGORY_PUBLICATION_TRANSPORT_FAILURE
    assert result.repair_decision is None


def test_route_or_halt_unrecognized_signal_raises():
    with pytest.raises(FailureClassificationError):
        route_or_halt("TOTALLY_UNKNOWN_SIGNAL")
