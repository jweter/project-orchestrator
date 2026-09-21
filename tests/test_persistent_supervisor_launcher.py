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


def test_existing_active_shape_requires_canonical_identity_agreement(tmp_path):
    write(tmp_path/"supervisor-reentry.json",{"status":"SUPERVISOR_INSTANCE_ALREADY_ACTIVE","active_process_instance_id":"live"})
    write(tmp_path/"supervisor-process-instance.json",{"process_instance_id":"live","burst_id":"burst"})
    write(tmp_path/"supervisor.json",{"process_instance_id":"stale","burst_id":"burst","updated_at_utc":"2026-01-01T00:00:00+00:00"})
    reentry=json.loads((tmp_path/"supervisor-reentry.json").read_text())
    proc=json.loads((tmp_path/"supervisor-process-instance.json").read_text())
    sup=json.loads((tmp_path/"supervisor.json").read_text())
    active_id=reentry["active_process_instance_id"]
    assert not (proc.get("process_instance_id")==active_id and sup.get("process_instance_id")==active_id)
