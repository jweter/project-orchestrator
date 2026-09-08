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
