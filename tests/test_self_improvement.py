import pytest

from project_orchestrator.self_improvement import build_self_improvement_proposals


def memory():
    return {
        "schema_version": 1,
        "entries": [{
            "project": "portfolio",
            "repository": "jweter/project-orchestrator",
            "source_type": "governance",
            "source_ref": "project-orchestrator#29",
            "symptom": "branch orchestration wrote directly to main",
            "root_cause": "branch promotion path was established too late",
            "fix": "require branch before mutation",
            "verification": "later PRs followed branch-first promotion",
            "regression_protection": "branch-first repository mutation guard",
            "residual_risk": "connector permission is not authority",
            "evidence_state": "VERIFIED",
        }],
    }


def store():
    metrics = {
        key: {"status": "UNKNOWN"}
        for key in (
            "pr_creation_to_green_seconds","failure_to_repair_seconds","merge_cycle_seconds",
            "blocked_waiting_seconds","ci_failure_count","ci_repeat_failure_count",
            "preflight_failures_prevented_count","roadmap_slices_completed_count",
            "product_reality_debt_created_count","product_reality_debt_resolved_count",
            "cross_repo_incident_count","autonomous_intervention_count","jeremy_required_intervention_count",
        )
    }
    return {"schema_version": 1, "windows": []}


def event(ref, friction="branch_reconciliation_friction", state="VERIFIED"):
    return {
        "repository": "jweter/project-orchestrator",
        "friction_type": friction,
        "evidence_ref": ref,
        "summary": "stale valuable branches require repeated manual reconciliation",
        "evidence_state": state,
    }


def test_requires_recurrence_before_proposing_self_improvement():
    result = build_self_improvement_proposals(memory(), store(), [event("run-1")])
    assert result["proposal_count"] == 0


def test_recurring_verified_friction_produces_bounded_proposal():
    result = build_self_improvement_proposals(memory(), store(), [event("run-1"), event("run-2")])
    proposal = result["proposals"][0]
    assert proposal["friction_type"] == "branch_reconciliation_friction"
    assert proposal["recurrence_count"] == 2
    assert proposal["predicted_roi"] == "UNKNOWN"
    assert proposal["predicted_time_saved"] == "UNKNOWN"
    assert result["execution_authority"] == "PROPOSAL_ONLY"


def test_unverified_events_do_not_count():
    result = build_self_improvement_proposals(
        memory(), store(), [event("run-1"), event("run-2", state="UNVERIFIED")]
    )
    assert result["proposal_count"] == 0


def test_deduplicates_repeated_evidence_refs():
    result = build_self_improvement_proposals(memory(), store(), [event("run-1"), event("run-1")])
    assert result["proposal_count"] == 0


def test_rejects_unknown_friction_class():
    with pytest.raises(ValueError, match="unsupported friction_type"):
        build_self_improvement_proposals(memory(), store(), [event("x", friction="magic")])


def test_requires_minimum_recurrence_at_least_two():
    with pytest.raises(ValueError, match="minimum_recurrence"):
        build_self_improvement_proposals(memory(), store(), [], minimum_recurrence=1)
