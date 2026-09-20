import json
from scripts.continuous_flow_supervisor import request_liveness_reacquire

def test_idle_eligible_lane_requests_immediate_reacquire(tmp_path):
    result=request_liveness_reacquire(tmp_path,{"status":"ELIGIBLE_IDLE","grant_evidence":[]})
    assert result["requested"] is True
    assert result["active_claim_present"] is False
    assert (tmp_path/"liveness-reacquire.json").exists()

def test_stale_active_lane_requests_scheduler_but_preserves_ownership(tmp_path):
    result=request_liveness_reacquire(tmp_path,{"status":"STALE_ACTIVE_WORK",
        "grant_evidence":[{"state":"CLAIMED","grant_id":"g"}]})
    assert result["requested"] is True
    assert result["active_claim_present"] is True
    persisted=json.loads((tmp_path/"liveness-reacquire.json").read_text())
    assert persisted["reason"]=="STALE_ACTIVE_WORK"

def test_productive_flow_does_not_request_reacquire(tmp_path):
    result=request_liveness_reacquire(tmp_path,{"status":"PRODUCTIVE","grant_evidence":[]})
    assert result=={"requested":False,"reason":"LIVENESS_RECOVERY_NOT_REQUIRED"}
    assert not (tmp_path/"liveness-reacquire.json").exists()

def test_terminal_no_work_does_not_request_reacquire(tmp_path):
    result=request_liveness_reacquire(tmp_path,{"status":"NO_AUTHORIZED_WORK","grant_evidence":[]})
    assert result["requested"] is False


def test_supervisor_source_reenters_immediately_on_reacquire():
    from pathlib import Path
    source=Path("scripts/continuous_flow_supervisor.py").read_text(encoding="utf-8")
    assert 'lifecycle_advanced or liveness_reacquire.get("requested")' in source
