from datetime import UTC, datetime, timedelta
import json
from scripts.continuous_flow_supervisor import productive_liveness

NOW=datetime(2026,9,20,12,0,tzinfo=UTC)

def write_grants(root,grants):
    (root/"hands-off-grant-lifecycle.json").write_text(json.dumps({"grants":grants}),encoding="utf-8")

def test_eligible_without_active_grant_is_idle(tmp_path):
    result=productive_liveness(tmp_path,["rocksmith"],NOW)
    assert result["status"]=="ELIGIBLE_IDLE"
    assert result["productive"] is False

def test_recent_active_grant_is_productive(tmp_path):
    write_grants(tmp_path,{"g":{"state":"CLAIMED","repository":"o/r","slice_id":"s",
        "head_sha":"a"*40,"last_productive_mutation_at":(NOW-timedelta(seconds=30)).isoformat()}})
    result=productive_liveness(tmp_path,["rocksmith"],NOW)
    assert result["status"]=="PRODUCTIVE"
    assert result["active_grants"][0]["slice_id"]=="s"

def test_stale_active_grant_is_not_productive(tmp_path):
    write_grants(tmp_path,{"g":{"state":"RUNNING","repository":"o/r",
        "last_productive_mutation_at":(NOW-timedelta(seconds=901)).isoformat()}})
    result=productive_liveness(tmp_path,["rocksmith"],NOW,stale_seconds=900)
    assert result["status"]=="STALE_ACTIVE_WORK"
    assert result["productive"] is False

def test_no_authorized_work_is_not_false_idle_alarm(tmp_path):
    result=productive_liveness(tmp_path,[],NOW)
    assert result["status"]=="NO_AUTHORIZED_WORK"
