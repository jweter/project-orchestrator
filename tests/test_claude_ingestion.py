import json
import time
import pytest
from project_orchestrator.claude_dispatch import seed_external_dispatch, ingest_session_result, specialist_scorecard

def test_ingest_scores_role_adherence_and_advancement(tmp_path):
    r=seed_external_dispatch(tmp_path,routine="portfolio_builder",repository="portfolio",task_key="test-1",
        session_id="cse_1",session_url="https://claude.ai/code/cse_1",started_at=time.time()-10)
    out=ingest_session_result(tmp_path,r.dispatch_id,outcome_kind="PRODUCT_PR",repository="jweter/knowledge-engine-core",
        pr_url="https://github.com/x/y/pull/1",verification="CI_GREEN",product_advancement=True)
    assert out["role_adherence"] is True
    score=specialist_scorecard(tmp_path)["portfolio_builder"]
    assert score["advancement_rate"] == 1.0
    assert score["human_intervention_rate"] == 0.0

def test_role_drift_fails_measurement(tmp_path):
    r=seed_external_dispatch(tmp_path,routine="repair_rescue",repository="portfolio",task_key="test-2",
        session_id="cse_2",session_url="https://claude.ai/code/cse_2",started_at=time.time())
    out=ingest_session_result(tmp_path,r.dispatch_id,outcome_kind="PRODUCT_PR",product_advancement=True)
    assert out["role_adherence"] is False
    assert out["status"] == "FAIL"

def test_noop_is_successful_conservative_behavior(tmp_path):
    r=seed_external_dispatch(tmp_path,routine="adversarial_reviewer",repository="portfolio",task_key="test-3",
        session_id="cse_3",session_url="https://claude.ai/code/cse_3",started_at=time.time())
    out=ingest_session_result(tmp_path,r.dispatch_id,outcome_kind="NO_OP")
    assert out["role_adherence"] is True
    assert out["status"] == "NO_OP"
