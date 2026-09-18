from datetime import datetime, timezone

import pytest

from project_orchestrator.closed_loop import validate_closed_loop_run


NOW = datetime(2026, 9, 8, 3, 0, tzinfo=timezone.utc)


def stages():
    return {
        stage: {"state": "COMPLETE", "evidence_ref": f"evidence:{stage.lower()}"}
        for stage in ("SENSE", "REMEMBER", "DECIDE", "ACT", "VERIFY", "LEARN", "ADAPT")
    }


def project(**overrides):
    item = {
        "eligibility": "ELIGIBLE",
        "advancement": "SUBSTANTIAL_SLICE_ADVANCED",
        "stages": stages(),
        "prior_lessons_reused": ["lesson-1"],
        "lessons_recorded": ["lesson-2"],
        "pre_change_risk_state": "NO_RELEVANT_VERIFIED_HISTORY",
        "pre_change_risk_evidence_ref": "risk:profile",
        "agent_action_guard_state": "READY",
        "agent_action_guard_evidence_ref": "action-guard:ready",
        "execution_evidence_ref": "commit:exact-head",
        "verification_evidence_ref": "preflight:green",
    }
    item.update(overrides)
    return item


def payload(**overrides):
    value = {
        "schema_version": 1,
        "observed_at": "2026-09-08T02:30:00Z",
        "max_evidence_age_seconds": 3600,
        "repositories_inspected": [
            "jweter/knowledge-engine-core",
            "jweter/knowledge-engine-web",
            "jweter/knowledge-engine-ai",
            "jweter/rocksmith-cdlc-generator",
            "jweter/Project-Everward",
        ],
        "projects": {
            "knowledge-engine": project(),
            "rocksmith": project(),
            "everward": project(),
        },
    }
    value.update(overrides)
    return value


def test_fresh_complete_run_closes_all_three_learning_loops():
    result = validate_closed_loop_run(payload(), now=NOW)
    assert result["evidence_fresh"] is True
    assert result["run_closed"] is True
    assert result["kpis"]["product_advancement_rate"]["value"] == 1.0
    assert result["kpis"]["closed_learning_loops"] == 3


def test_stale_evidence_blocks_trend_claims():
    result = validate_closed_loop_run(
        payload(observed_at="2026-09-07T20:00:00Z"),
        now=NOW,
    )
    assert result["evidence_fresh"] is False
    assert result["trend_claim_authorized"] is False
    assert result["run_closed"] is False


def test_eligible_project_must_remember_before_acting():
    bad = stages()
    bad["REMEMBER"] = {"state": "UNKNOWN"}
    projects = payload()["projects"]
    projects["rocksmith"] = project(stages=bad)
    with pytest.raises(ValueError, match="REMEMBER"):
        validate_closed_loop_run(payload(projects=projects), now=NOW)


def test_advancement_requires_verification():
    bad = stages()
    bad["VERIFY"] = {"state": "BLOCKED", "reason": "preflight unavailable"}
    projects = payload()["projects"]
    projects["everward"] = project(stages=bad)
    with pytest.raises(ValueError, match="ACT and VERIFY"):
        validate_closed_loop_run(payload(projects=projects), now=NOW)


def test_no_advancement_requires_reason():
    projects = payload()["projects"]
    projects["knowledge-engine"] = project(advancement="NONE")
    with pytest.raises(ValueError, match="no_advancement_reason"):
        validate_closed_loop_run(payload(projects=projects), now=NOW)


def test_unknown_learning_stage_does_not_close_run():
    bad = stages()
    bad["LEARN"] = {"state": "UNKNOWN"}
    projects = payload()["projects"]
    projects["knowledge-engine"] = project(stages=bad)
    result = validate_closed_loop_run(payload(projects=projects), now=NOW)
    assert result["run_closed"] is False
    assert result["kpis"]["closed_learning_loops"] == 2


def test_no_safe_work_can_close_without_fake_advancement():
    item = project(eligibility="NO_SAFE_ELIGIBLE_WORK", advancement="NONE")
    s = item["stages"]
    s["ACT"] = {"state": "NOT_APPLICABLE"}
    s["VERIFY"] = {"state": "NOT_APPLICABLE"}
    projects = payload()["projects"]
    projects["rocksmith"] = item
    result = validate_closed_loop_run(payload(projects=projects), now=NOW)
    assert result["kpis"]["product_advancement_rate"]["denominator"] == 2


def test_eligible_project_requires_pre_change_risk_profile():
    projects = payload()["projects"]
    projects["everward"] = project(
        pre_change_risk_state=None,
        pre_change_risk_evidence_ref=None,
    )
    with pytest.raises(ValueError, match="pre_change_risk_state"):
        validate_closed_loop_run(payload(projects=projects), now=NOW)


def test_explicit_no_history_is_valid_pre_change_risk_evidence():
    result = validate_closed_loop_run(payload(), now=NOW)
    assert result["projects"]["everward"]["pre_change_risk_state"] == "NO_RELEVANT_VERIFIED_HISTORY"


def test_eligible_project_requires_action_guard_evidence():
    projects = payload()["projects"]
    projects["knowledge-engine"] = project(
        agent_action_guard_state=None,
        agent_action_guard_evidence_ref=None,
    )
    with pytest.raises(ValueError, match="agent_action_guard_state"):
        validate_closed_loop_run(payload(projects=projects), now=NOW)


def test_act_cannot_complete_when_action_guard_is_not_ready():
    projects = payload()["projects"]
    projects["knowledge-engine"] = project(agent_action_guard_state="GATHER_CONTEXT")
    with pytest.raises(ValueError, match="ACT=COMPLETE requires agent_action_guard_state=READY"):
        validate_closed_loop_run(payload(projects=projects), now=NOW)


def test_non_ready_action_guard_can_block_without_fake_advancement():
    item = project(
        advancement="NONE",
        agent_action_guard_state="APPROVAL_REQUIRED",
        no_advancement_reason="paid external cost requires approval",
    )
    s = item["stages"]
    s["ACT"] = {"state": "BLOCKED", "reason": "Agent Action Guard requires approval"}
    s["VERIFY"] = {"state": "NOT_APPLICABLE"}
    projects = payload()["projects"]
    projects["rocksmith"] = item
    result = validate_closed_loop_run(payload(projects=projects), now=NOW)
    assert result["projects"]["rocksmith"]["agent_action_guard_state"] == "APPROVAL_REQUIRED"
    assert result["kpis"]["eligible_projects_advanced"] == 2


def test_advancement_requires_explicit_execution_reference():
    projects = payload()["projects"]
    projects["rocksmith"] = project(execution_evidence_ref=None)
    with pytest.raises(ValueError, match="execution_evidence_ref"):
        validate_closed_loop_run(payload(projects=projects), now=NOW)


def test_advancement_requires_explicit_verification_reference():
    projects = payload()["projects"]
    projects["knowledge-engine"] = project(verification_evidence_ref=None)
    with pytest.raises(ValueError, match="verification_evidence_ref"):
        validate_closed_loop_run(payload(projects=projects), now=NOW)


def test_closed_loop_applies_retained_learning_to_real_scheduler_context():
    projects = payload()["projects"]
    projects["knowledge-engine"] = project(
        policy_input={
            "repository": "jweter/knowledge-engine-core",
            "changed_paths": ["scripts/worker.py"],
            "failure_class": "ENVIRONMENT_FAILURE",
        }
    )
    projects["rocksmith"] = project(
        policy_input={
            "repository": "jweter/rocksmith-cdlc-generator",
            "changed_paths": ["src/cli.py"],
        }
    )
    projects["everward"] = project(
        policy_input={
            "repository": "jweter/Project-Everward",
            "changed_paths": ["tools/worker.py"],
        }
    )
    store = {
        "schema_version": 1,
        "rules": [
            {
                "rule_id": "rule-provision-first",
                "source_experiment_id": "exp-1",
                "repository": "jweter/knowledge-engine-core",
                "scope": "repository",
                "mechanism": "provision before verify",
                "applicability": {
                    "path_prefixes": ["scripts/"],
                    "failure_classes": ["ENVIRONMENT_FAILURE"],
                },
                "evidence_refs": ["experiment:exp-1"],
                "status": "VERIFIED_RETAINED",
                "authority": "ADVISORY_TO_SCHEDULER_NORMAL_GATES_REQUIRED",
                "safety_invariants": [],
            }
        ],
    }
    result = validate_closed_loop_run(
        payload(projects=projects),
        now=NOW,
        retained_learning_store=store,
    )
    policy = result["projects"]["knowledge-engine"]["retained_policy"]
    assert policy["applied_rule_ids"] == ["rule-provision-first"]
    assert policy["mechanisms"] == ["provision before verify"]
    assert result["projects"]["rocksmith"]["retained_policy"]["applied_rule_ids"] == []


def test_retained_learning_enabled_fails_closed_without_policy_input():
    with pytest.raises(ValueError, match="policy_input"):
        validate_closed_loop_run(
            payload(),
            now=NOW,
            retained_learning_store={"schema_version": 1, "rules": []},
        )
