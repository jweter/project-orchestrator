import json
from datetime import UTC, datetime, timedelta
from scripts.continuous_flow_supervisor import start_or_resume_burst

NOW=datetime(2026,9,20,14,0,tzinfo=UTC)

def write_current(root, **overrides):
    data={"schema_version":1,"burst_id":"wake-existing","pass_count":3,
          "immediate_continuation":True,"updated_at_utc":(NOW-timedelta(seconds=30)).isoformat()}
    data.update(overrides)
    (root/"continuation-current-burst.json").write_text(json.dumps(data),encoding="utf-8")

def test_recent_interrupted_immediate_burst_resumes_identity_and_pass_count(tmp_path):
    write_current(tmp_path)
    result=start_or_resume_burst(tmp_path,now=NOW,resume_seconds=900)
    assert result["resumed"] is True
    assert result["burst_id"]=="wake-existing"
    assert result["burst_pass"]==3

def test_normal_backoff_is_a_new_wake_not_a_resume(tmp_path):
    write_current(tmp_path,immediate_continuation=False)
    result=start_or_resume_burst(tmp_path,now=NOW)
    assert result["resumed"] is False
    assert result["burst_id"]!="wake-existing"

def test_stale_interrupted_burst_fails_closed_to_new_identity(tmp_path):
    write_current(tmp_path,updated_at_utc=(NOW-timedelta(seconds=901)).isoformat())
    result=start_or_resume_burst(tmp_path,now=NOW,resume_seconds=900)
    assert result["resumed"] is False
    assert result["burst_id"]!="wake-existing"

def test_corrupt_state_fails_closed_to_new_identity(tmp_path):
    (tmp_path/"continuation-current-burst.json").write_text("{bad",encoding="utf-8")
    result=start_or_resume_burst(tmp_path,now=NOW)
    assert result["resumed"] is False
    assert result["burst_pass"]==0
