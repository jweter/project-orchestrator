import json
import os
import sys
from pathlib import Path
from datetime import UTC, datetime, timedelta
from scripts.launch_persistent_supervisor import (request_stale_generation_handoff, supervisor_command, supervisor_environment, supervisor_state_fresh, verified_identity)

def write(path:Path,payload:dict):
    path.write_text(json.dumps(payload),encoding="utf-8")

def test_supervisor_command_uses_package_module_for_scripts_imports():
    repo=Path(r"C:\repo")
    state=repo/".hands-off-state"
    cmd=supervisor_command(repo,r"C:\workspace",state,60,30)
    assert cmd[:3]==[sys.executable,"-m","scripts.continuous_flow_supervisor"]
    assert str(repo/"scripts"/"continuous_flow_supervisor.py") not in cmd
    assert cmd[cmd.index("--workspace-root")+1]==r"C:\workspace"
    assert cmd[cmd.index("--state-root")+1]==str(state)

def test_supervisor_environment_prepends_src_to_existing_pythonpath(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", r"C:\existing")
    env=supervisor_environment(Path(r"C:\repo"))
    assert env["PYTHONPATH"] == str(Path(r"C:\repo")/"src") + os.pathsep + r"C:\existing"

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


def test_supervisor_state_fresh_rejects_alive_but_stale_generation():
    now=datetime(2026,10,2,1,0,tzinfo=UTC)
    assert supervisor_state_fresh({"updated_at_utc":(now-timedelta(seconds=30)).isoformat()},now=now,max_age_seconds=60)
    assert not supervisor_state_fresh({"updated_at_utc":(now-timedelta(seconds=61)).isoformat()},now=now,max_age_seconds=60)

def test_stale_generation_handoff_request_is_fenced_to_predecessor(tmp_path):
    now=datetime(2026,10,2,1,0,tzinfo=UTC)
    payload=request_stale_generation_handoff(tmp_path,"old-generation",now=now)
    persisted=json.loads((tmp_path/"supervisor-takeover-request.json").read_text())
    assert payload==persisted
    assert persisted["status"]=="TAKEOVER_REQUESTED"
    assert persisted["predecessor_process_instance_id"]=="old-generation"
    assert persisted["requested_at_utc"]==now.isoformat()
