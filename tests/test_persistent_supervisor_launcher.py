import json
from pathlib import Path
from scripts.launch_persistent_supervisor import verified_identity

def write(path:Path,payload:dict):
    path.write_text(json.dumps(payload),encoding="utf-8")

def test_verified_identity_requires_new_matching_process_and_burst(tmp_path):
    write(tmp_path/"supervisor-process-instance.json",{"process_instance_id":"new","burst_id":"burst"})
    write(tmp_path/"supervisor.json",{"process_instance_id":"new","burst_id":"burst","updated_at_utc":"2026-01-01T00:00:00+00:00","status":"FLOW_DEGRADED","builder_status":"USER_ACTIVE"})
    result=verified_identity(tmp_path,"old")
    assert result["process_instance_id"]=="new"
    assert result["burst_id"]=="burst"

def test_verified_identity_rejects_stale_previous_process(tmp_path):
    write(tmp_path/"supervisor-process-instance.json",{"process_instance_id":"old","burst_id":"burst"})
    write(tmp_path/"supervisor.json",{"process_instance_id":"old","burst_id":"burst","updated_at_utc":"2026-01-01T00:00:00+00:00"})
    assert verified_identity(tmp_path,"old") is None

def test_verified_identity_rejects_mismatched_supervisor_state(tmp_path):
    write(tmp_path/"supervisor-process-instance.json",{"process_instance_id":"new","burst_id":"burst-a"})
    write(tmp_path/"supervisor.json",{"process_instance_id":"new","burst_id":"burst-b","updated_at_utc":"2026-01-01T00:00:00+00:00"})
    assert verified_identity(tmp_path,"old") is None
