from __future__ import annotations

import pytest

from project_orchestrator.flow_signal import (
    build_reconciliation_pr_plan,
    build_stale_promotion_recovery,
    classify_workflow_outcome,
    compute_diagnostic_churn,
)


A = "a" * 40
B = "b" * 40


def test_non_applicable_workflow_is_neutral_not_failure() -> None:
    result = classify_workflow_outcome({"applicable": False})
    assert result["operational_state"] == "NOT_APPLICABLE"
    assert result["dashboard_severity"] == "NEUTRAL"
    assert result["counts_as_product_failure"] is False
    assert result["fail_closed"] is False


def test_stale_head_is_first_class_safety_state() -> None:
    result = classify_workflow_outcome(
        {
            "applicable": True,
            "expected_head_sha": A,
            "actual_head_sha": B,
            "workflow_status": "completed",
            "workflow_conclusion": "failure",
        }
    )
    assert result["operational_state"] == "PROMOTION_STALE"
    assert result["dashboard_severity"] == "YELLOW"
    assert result["counts_as_product_failure"] is False
    assert result["requires_code_repair"] is False
    assert result["fail_closed"] is True
    assert result["next_action"] == "REVALIDATE_CURRENT_HEAD"


def test_zero_job_action_required_remains_platform_gate() -> None:
    result = classify_workflow_outcome(
        {
            "applicable": True,
            "workflow_status": "completed",
            "workflow_conclusion": "action_required",
            "jobs_count": 0,
            "log_text": "",
        }
    )
    assert result["operational_state"] == "PLATFORM_GATE"
    assert result["failure_class"] == "PLATFORM_GATE"
    assert result["counts_as_product_failure"] is False
    assert result["dashboard_severity"] == "RED"


def test_real_test_failure_stays_red_and_repairable() -> None:
    result = classify_workflow_outcome(
        {
            "applicable": True,
            "workflow_status": "completed",
            "workflow_conclusion": "failure",
            "log_text": "FAILED tests/test_x.py::test_y AssertionError",
        }
    )
    assert result["operational_state"] == "ACTIONABLE_FAILURE"
    assert result["failure_class"] == "TEST_FAILURE"
    assert result["counts_as_product_failure"] is True
    assert result["requires_code_repair"] is True


def test_stale_recovery_requires_new_preflight_when_current_head_unverified() -> None:
    result = build_stale_promotion_recovery(
        {
            "repository": "owner/repo",
            "branch": "feature/x",
            "expected_head_sha": A,
            "current_head_sha": B,
            "verified_head_sha": A,
        }
    )
    assert result["state"] == "REVERIFY_REQUIRED"
    assert result["preflight_reuse_allowed"] is False
    assert result["merge_allowed"] is False
    assert result["next_action"] == "RUN_FULL_EXACT_HEAD_PREFLIGHT"


def test_stale_recovery_can_reuse_exact_current_head_evidence_only() -> None:
    result = build_stale_promotion_recovery(
        {
            "repository": "owner/repo",
            "branch": "feature/x",
            "expected_head_sha": A,
            "current_head_sha": B,
            "verified_head_sha": B,
        }
    )
    assert result["state"] == "HEAD_MOVED_BUT_CURRENT_HEAD_ALREADY_VERIFIED"
    assert result["preflight_reuse_allowed"] is True
    assert result["merge_allowed"] is False
    assert result["next_action"] == "REVALIDATE_CI_REVIEWS_AND_MERGEABILITY"


def test_bot_reconciliation_branch_requires_orchestrator_materialization() -> None:
    result = build_reconciliation_pr_plan(
        {
            "repository": "jweter/knowledge-engine-ai",
            "branch": "automation/status-reconcile",
            "head_sha": A,
            "source_actor": "github-actions[bot]",
            "authorized_branch_prefixes": ["automation/status-reconcile"],
            "open_pr_number": None,
            "branch_pushed": True,
        }
    )
    assert result["schema_version"] == 2
    assert result["action"] == "MATERIALIZE_ORCHESTRATOR_PROMOTION_BRANCH"
    assert result["materialization_required"] is True
    assert result["github_actions_may_create_pr"] is False
    assert result["merge_allowed"] is False
    assert "full exact-head preflight" in result["next_gate"]


def test_reconciliation_pr_plan_fails_closed_for_unauthorized_branch() -> None:
    result = build_reconciliation_pr_plan(
        {
            "repository": "owner/repo",
            "branch": "feature/unrelated",
            "head_sha": A,
            "source_actor": "github-actions[bot]",
            "authorized_branch_prefixes": ["automation/status-reconcile"],
            "open_pr_number": None,
            "branch_pushed": True,
        }
    )
    assert result["action"] == "BLOCKED_UNAUTHORIZED_BRANCH"
    assert result["materialization_required"] is False
    assert result["merge_allowed"] is False


def test_diagnostic_churn_uses_only_explicit_evidence() -> None:
    result = compute_diagnostic_churn(
        [
            {
                "diagnostic_investigation": True,
                "known_non_actionable_before_investigation": True,
            },
            {
                "diagnostic_investigation": True,
                "known_non_actionable_before_investigation": False,
            },
            {
                "diagnostic_investigation": False,
                "known_non_actionable_before_investigation": True,
            },
        ]
    )
    assert result["avoidable_diagnostic_churn_count"]["value"] == 1
    assert result["avoidable_diagnostic_churn_rate"]["value"] == 0.5
    assert result["avoidable_diagnostic_churn_rate"]["sample_count"] == 2


def test_diagnostic_churn_missing_evidence_is_unknown() -> None:
    result = compute_diagnostic_churn([])
    assert result["avoidable_diagnostic_churn_count"]["status"] == "UNKNOWN"
    assert result["avoidable_diagnostic_churn_rate"]["status"] == "UNKNOWN"


def test_diagnostic_churn_rejects_partial_boolean_evidence() -> None:
    with pytest.raises(ValueError, match="must be booleans"):
        compute_diagnostic_churn([{"diagnostic_investigation": True}])
