import json
from datetime import UTC, datetime
import scripts.continuous_flow_supervisor as supervisor

def test_resume_preserves_logical_burst_but_rotates_physical_instance(tmp_path):
    now=datetime.now(UTC)
    path=tmp_path/"continuation-current-burst.json"
    path.write_text(json.dumps({"schema_version":2,"burst_id":"burst-a","process_instance_id":"old",
        "pass_count":7,"immediate_continuation":True,"updated_at_utc":now.isoformat()}))
    result=supervisor.start_or_resume_burst(tmp_path,process_instance_id="new",now=now)
    assert result["burst_id"]=="burst-a"
    assert result["burst_pass"]==7
    assert result["resumed"] is True
    assert result["previous_process_instance_id"]=="old"
    assert result["process_instance_id"]=="new"

def test_new_burst_records_current_physical_instance(tmp_path):
    now=datetime.now(UTC)
    result=supervisor.start_or_resume_burst(tmp_path,process_instance_id="instance-a",now=now)
    assert result["resumed"] is False
    assert result["process_instance_id"]=="instance-a"
    assert result["previous_process_instance_id"] is None

def test_instance_lock_assigns_distinct_physical_identity_after_release(tmp_path):
    first=supervisor.acquire_supervisor_instance_lock(tmp_path)
    assert first is not None
    first_id=first["worker"]["instance_id"]
    assert supervisor.acquire_supervisor_instance_lock(tmp_path) is None
    supervisor.release_supervisor_instance_lock(first)
    second=supervisor.acquire_supervisor_instance_lock(tmp_path)
    try:
        assert second is not None
        assert second["worker"]["instance_id"]!=first_id
    finally:
        supervisor.release_supervisor_instance_lock(second)
