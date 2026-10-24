"""IG-035 fail-closed routing from Phase 3 failures into repair lanes."""

import pytest

from project_orchestrator.local_repair_routing import route_phase3_failure


@pytest.mark.parametrize(
    ("diagnostic", "lane", "action"),
    [
        (
            "OWNER_CONTROL_VETO",
            "OWNER_HANDOFF",
            "WAIT_FOR_EXPLICIT_OWNER_RELEASE",
        ),
        (
            "VERIFICATION_FAILED:PRODUCT_REALITY_ADAPTER_REQUIRED",
            "PRODUCT_REALITY_REPAIR",
            "RUN_REPOSITORY_PRODUCT_REALITY_ADAPTER",
        ),
        (
            "INTEGRATION_CONFLICT",
            "INTEGRATION_CONFLICT_REPAIR",
            "REPAIR_CONFLICT_ON_NEW_CANDIDATE",
        ),
        (
            "ACCEPTED_BASE_CHANGED_AFTER_MERGE",
            "STALE_BASE_REINTEGRATION",
            "RELOAD_ACCEPTED_BASE_AND_REINTEGRATE",
        ),
        (
            "STATE_PREPARE_FAILED:REPOSITORY_ACCEPTANCE_LANE_OCCUPIED",
            "ACCEPTANCE_RECONCILIATION",
            "RECONCILE_DURABLE_ACCEPTANCE_INTENT",
        ),
        (
            "RECOVERY_STOP:MILESTONE_REF_MISSING_STOP",
            "AUTHORITY_RECONCILIATION",
            "RECONCILE_PROTECTED_GIT_AUTHORITY",
        ),
        (
            "VERIFICATION_RECEIPT_CHANGED",
            "VERIFICATION_REPAIR",
            "REPAIR_THEN_RERUN_EXACT_HEAD_FULL_PREFLIGHT",
        ),
        (
            "GIT_EXECUTION_FAILED",
            "INFRASTRUCTURE_REPAIR",
            "PRESERVE_STATE_AND_REPAIR_LOCAL_EXECUTION",
        ),
    ],
)
def test_known_failures_route_to_one_explicit_repair_lane(diagnostic, lane, action):
    directive = route_phase3_failure(diagnostic)
    assert directive.lane == lane
    assert directive.action == action
    assert directive.preserve_candidate is True


def test_owner_handoff_requires_explicit_release():
    directive = route_phase3_failure(ValueError("OWNER_CONTROL_VETO"))
    assert directive.owner_release_required is True
    assert directive.retry_after_repair is True


def test_product_reality_routes_to_adapter_not_bypass():
    directive = route_phase3_failure("PRODUCT_REALITY_NOT_CLEARED")
    assert directive.lane == "PRODUCT_REALITY_REPAIR"
    assert "ADAPTER" in directive.action
    assert "BYPASS" not in directive.action


def test_unclassified_failure_stops_for_triage():
    directive = route_phase3_failure("mystery failure")
    assert directive.lane == "MANUAL_TRIAGE"
    assert directive.retry_after_repair is False
    assert directive.action == "STOP_AND_CAPTURE_UNCLASSIFIED_DIAGNOSTIC"


def test_authority_conflict_is_not_blindly_retried():
    directive = route_phase3_failure("MILESTONE_REF_CONFLICT")
    assert directive.lane == "AUTHORITY_RECONCILIATION"
    assert directive.retry_after_repair is False
