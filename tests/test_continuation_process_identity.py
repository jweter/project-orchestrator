import json
from datetime import UTC, datetime, timedelta
from scripts.continuous_flow_supervisor import (
    record_continuation_pass, start_or_resume_burst, record_burst_resume_event
)

NOW=datetime(2026,9,20,15,0,tzinfo=UTC)

def test_restart_preserves_logical_burst_but_changes_physical_process(tmp_path):
    prior={"schema_version":2,"burst_id":"wake-1","process_instance_id":"proc-old",
           "pass_count":4,"immediate_continuation":True,
           "updated_at_utc":(NOW-timedelta(seconds=20)).isoformat()}
    (tmp_path/"continuation-current-burst.json").write_text(json.dumps(prior),encoding="utf-8")
    resumed=start_or_resume_burst(tmp_path,now=NOW,resume_seconds=900,process_instance_id="proc-new")
    assert resumed["resumed"] is True
    assert resumed["burst_id"]=="wake-1"
    assert resumed["process_instance_id"]=="proc-new"
    assert resumed["resumed_from_process_instance_id"]=="proc-old"
    assert resumed["burst_pass"]==4

def test_new_wake_has_new_process_without_resume_parent(tmp_path):
    burst=start_or_resume_burst(tmp_path,now=NOW,process_instance_id="proc-1")
    assert burst["resumed"] is False
    assert burst["process_instance_id"]=="proc-1"
    assert burst["resumed_from_process_instance_id"] is None

def test_pass_history_records_physical_process_identity(tmp_path):
    record=record_continuation_pass(tmp_path,builder_status="PR_OPEN",delay_seconds=1,
        lifecycle_advanced=False,liveness_reacquire={"requested":False},
        burst_id="wake-1",process_instance_id="proc-9")
    assert record["burst_id"]=="wake-1"
    assert record["process_instance_id"]=="proc-9"
    history=json.loads((tmp_path/"continuation-pass-history.json").read_text())
    assert history["passes"][-1]["process_instance_id"]=="proc-9"

def test_resume_event_links_old_and_new_processes(tmp_path):
    burst={"resumed":True,"burst_id":"wake-1","process_instance_id":"proc-new",
           "resumed_from_process_instance_id":"proc-old","resume_age_seconds":12.5}
    event=record_burst_resume_event(tmp_path,burst)
    assert event["from_process_instance_id"]=="proc-old"
    assert event["to_process_instance_id"]=="proc-new"
    payload=json.loads((tmp_path/"continuation-resume-events.json").read_text())
    assert payload["event_count"]==1
    assert payload["events"][0]["burst_id"]=="wake-1"

def test_new_wake_does_not_emit_resume_event(tmp_path):
    assert record_burst_resume_event(tmp_path,{"resumed":False,"burst_id":"wake-2"}) is None
    assert not (tmp_path/"continuation-resume-events.json").exists()
