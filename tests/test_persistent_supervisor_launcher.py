import json
import os
import sys
from pathlib import Path
from datetime import UTC, datetime, timedelta
from scripts.launch_persistent_supervisor import (atomic, network_guardian_command, publication_blocked_generation_live, publication_blocked_generation_stale, reclaim_stalled_generation, request_stale_generation_handoff, stale_generation_requires_reclaim, supervisor_command, supervisor_environment, supervisor_state_fresh, verified_identity)

def write(path:Path,payload:dict):
    path.write_text(json.dumps(payload),encoding="utf-8")

def test_atomic_uses_unique_temp_file_when_legacy_temp_is_contended(tmp_path):
    target=tmp_path/"persistent-supervisor-launch.json"
    legacy=target.with_suffix(target.suffix+".tmp")
    legacy.write_text("contended",encoding="utf-8")
    atomic(target,{"status":"PASS"})
    assert json.loads(target.read_text(encoding="utf-8"))=={"status":"PASS"}
    assert legacy.read_text(encoding="utf-8")=="contended"

def test_supervisor_command_uses_package_module_for_scripts_imports():
    repo=Path(r"C:\repo")
    state=repo/".hands-off-state"
    cmd=supervisor_command(repo,r"C:\workspace",state,60,30)
    assert cmd[:3]==[sys.executable,"-m","scripts.continuous_flow_supervisor"]
    assert str(repo/"scripts"/"continuous_flow_supervisor.py") not in cmd
    assert cmd[cmd.index("--workspace-root")+1]==r"C:\workspace"
    assert cmd[cmd.index("--state-root")+1]==str(state)

def test_network_guardian_is_part_of_existing_launcher_path():
    repo=Path(r"C:\repo")
    state=repo/".hands-off-state"
    cmd=network_guardian_command(repo,state)
    assert cmd[0]==sys.executable
    assert cmd[1]==str(repo/"scripts"/"network_self_heal.py")
    assert "--state-root" in cmd
    assert "--allow-reboot" in cmd
    assert cmd[cmd.index("--failures")+1]=="3"

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

def test_publication_blocked_generation_live_requires_matching_fresh_generation(tmp_path):
    now=datetime(2026,10,3,0,20,tzinfo=UTC)
    write(tmp_path/"supervisor-process-instance.json",{"process_instance_id":"live","burst_id":"burst"})
    write(tmp_path/"supervisor-generation-heartbeat.json",{
        "process_instance_id":"live","burst_id":"burst","status":"STATE_PUBLICATION_BLOCKED",
        "heartbeat_at_utc":(now-timedelta(seconds=10)).isoformat(),
    })
    assert publication_blocked_generation_live(tmp_path,"live",now=now,max_age_seconds=60)
    assert not publication_blocked_generation_live(tmp_path,"other",now=now,max_age_seconds=60)


def test_publication_blocked_generation_live_rejects_stale_or_nonblocked_heartbeat(tmp_path):
    now=datetime(2026,10,3,0,20,tzinfo=UTC)
    write(tmp_path/"supervisor-process-instance.json",{"process_instance_id":"live","burst_id":"burst"})
    write(tmp_path/"supervisor-generation-heartbeat.json",{
        "process_instance_id":"live","burst_id":"burst","status":"STATE_PUBLICATION_BLOCKED",
        "heartbeat_at_utc":(now-timedelta(seconds=61)).isoformat(),
    })
    assert not publication_blocked_generation_live(tmp_path,"live",now=now,max_age_seconds=60)
    write(tmp_path/"supervisor-generation-heartbeat.json",{
        "process_instance_id":"live","burst_id":"burst","status":"RUNNING_CURRENT",
        "heartbeat_at_utc":now.isoformat(),
    })
    assert not publication_blocked_generation_live(tmp_path,"live",now=now,max_age_seconds=60)


def test_publication_blocked_generation_stale_routes_expired_matching_generation(tmp_path):
    now=datetime(2026,10,3,0,20,tzinfo=UTC)
    write(tmp_path/"supervisor-process-instance.json",{"process_instance_id":"live","burst_id":"burst"})
    write(tmp_path/"supervisor-generation-heartbeat.json",{
        "process_instance_id":"live","burst_id":"burst","status":"STATE_PUBLICATION_BLOCKED",
        "heartbeat_at_utc":(now-timedelta(seconds=61)).isoformat(),
    })
    assert publication_blocked_generation_stale(tmp_path,"live",now=now,max_age_seconds=60)
    assert not publication_blocked_generation_stale(tmp_path,"other",now=now,max_age_seconds=60)


def test_stale_generation_handoff_request_is_fenced_to_predecessor(tmp_path):
    now=datetime(2026,10,2,1,0,tzinfo=UTC)
    payload=request_stale_generation_handoff(tmp_path,"old-generation",now=now)
    persisted=json.loads((tmp_path/"supervisor-takeover-request.json").read_text())
    assert payload==persisted
    assert persisted["status"]=="TAKEOVER_REQUESTED"
    assert persisted["predecessor_process_instance_id"]=="old-generation"
    assert persisted["requested_at_utc"]==now.isoformat()


def test_reclaim_stalled_generation_fences_exact_verified_owner(tmp_path):
    now=datetime(2026,10,3,1,0,tzinfo=UTC)
    write(tmp_path/"supervisor-process-instance.json",{"process_instance_id":"old","burst_id":"burst","worker":{"pid":42,"process_start_fingerprint":"windows:123"}})
    write(tmp_path/"supervisor-generation-heartbeat.json",{"process_instance_id":"old","burst_id":"burst","status":"RUNNING_CURRENT","heartbeat_at_utc":(now-timedelta(seconds=121)).isoformat()})
    write(tmp_path/"supervisor-progress.json",{"process_instance_id":"old","burst_id":"burst","sequence":7,"observed_at_utc":(now-timedelta(seconds=601)).isoformat()})
    # First observation starts the bounded stall confirmation window.
    first=reclaim_stalled_generation(
        tmp_path,"old",now=now,fingerprint_fn=lambda pid:"windows:123",
        retire_fn=lambda verified,**kwargs:{"status":"PROCESS_RETIRED","retired":True,"pid":verified["pid"]})
    assert first["status"]=="STALL_CONFIRMING"
    later=now+timedelta(seconds=31)
    result=reclaim_stalled_generation(
        tmp_path,"old",now=later,fingerprint_fn=lambda pid:"windows:123",
        retire_fn=lambda verified,**kwargs:{"status":"PROCESS_RETIRED","retired":True,"pid":verified["pid"]})
    assert result["status"]=="GENERATION_FENCED"
    assert result["reclaimed"] is True
    fence=json.loads((tmp_path/"supervisor-generation-fence.json").read_text())
    assert fence["process_instance_id"]=="old"
    assert fence["burst_id"]=="burst"

def test_reclaim_stalled_generation_refuses_pid_reuse(tmp_path):
    now=datetime(2026,10,3,1,0,tzinfo=UTC)
    write(tmp_path/"supervisor-process-instance.json",{"process_instance_id":"old","burst_id":"burst","worker":{"pid":42,"process_start_fingerprint":"windows:123"}})
    write(tmp_path/"supervisor-generation-heartbeat.json",{"process_instance_id":"old","burst_id":"burst","status":"RUNNING_CURRENT","heartbeat_at_utc":(now-timedelta(seconds=121)).isoformat()})
    write(tmp_path/"supervisor-progress.json",{"process_instance_id":"old","burst_id":"burst","sequence":7,"observed_at_utc":(now-timedelta(seconds=601)).isoformat()})
    first=reclaim_stalled_generation(tmp_path,"old",now=now,fingerprint_fn=lambda pid:"windows:999")
    assert first["status"]=="STALL_CONFIRMING"
    result=reclaim_stalled_generation(tmp_path,"old",now=now+timedelta(seconds=31),fingerprint_fn=lambda pid:"windows:999")
    assert result["status"]=="PROCESS_IDENTITY_CHANGED"
    assert result["reclaimed"] is False
    assert not (tmp_path/"supervisor-generation-fence.json").exists()

def test_reclaim_stalled_generation_refuses_recent_progress(tmp_path):
    now=datetime(2026,10,3,1,0,tzinfo=UTC)
    write(tmp_path/"supervisor-process-instance.json",{"process_instance_id":"old","burst_id":"burst","worker":{"pid":42,"process_start_fingerprint":"windows:123"}})
    write(tmp_path/"supervisor-generation-heartbeat.json",{"process_instance_id":"old","burst_id":"burst","status":"RUNNING_CURRENT","heartbeat_at_utc":(now-timedelta(seconds=121)).isoformat()})
    write(tmp_path/"supervisor-progress.json",{"process_instance_id":"old","burst_id":"burst","sequence":8,"observed_at_utc":(now-timedelta(seconds=10)).isoformat()})
    result=reclaim_stalled_generation(tmp_path,"old",now=now,fingerprint_fn=lambda pid:"windows:123")
    assert result["status"]=="NO_RECOVERY"
    assert result["reclaimed"] is False
    assert not (tmp_path/"supervisor-generation-fence.json").exists()

def test_stale_generation_reclaim_does_not_depend_on_canonical_supervisor_snapshot(tmp_path):
    now=datetime(2026,10,6,16,0,tzinfo=UTC)
    write(tmp_path/"supervisor-process-instance.json",{
        "process_instance_id":"current","burst_id":"burst","worker":{"pid":42,"process_start_fingerprint":"windows:123"}})
    write(tmp_path/"supervisor-generation-heartbeat.json",{
        "process_instance_id":"current","burst_id":"burst","status":"RUNNING_CURRENT",
        "heartbeat_at_utc":(now-timedelta(seconds=121)).isoformat()})
    write(tmp_path/"supervisor-progress.json",{
        "process_instance_id":"current","burst_id":"burst","sequence":7,
        "observed_at_utc":(now-timedelta(seconds=601)).isoformat()})
    # This is the production failure mode: canonical publication is from an
    # older generation, so it must not suppress reclaim of the proven owner.
    write(tmp_path/"supervisor.json",{
        "process_instance_id":"older","burst_id":"older-burst","updated_at_utc":now.isoformat()})
    assert stale_generation_requires_reclaim(
        tmp_path,"current",now=now,max_age_seconds=60) is True


def test_stale_generation_reclaim_requires_matching_process_and_heartbeat_identity(tmp_path):
    now=datetime(2026,10,6,16,0,tzinfo=UTC)
    write(tmp_path/"supervisor-process-instance.json",{"process_instance_id":"current","burst_id":"burst"})
    write(tmp_path/"supervisor-generation-heartbeat.json",{
        "process_instance_id":"other","burst_id":"burst",
        "heartbeat_at_utc":(now-timedelta(seconds=121)).isoformat()})
    assert stale_generation_requires_reclaim(
        tmp_path,"current",now=now,max_age_seconds=60) is False


def test_stale_generation_reclaim_rejects_unreadable_or_future_heartbeat(tmp_path):
    now=datetime(2026,10,6,16,0,tzinfo=UTC)
    write(tmp_path/"supervisor-process-instance.json",{
        "process_instance_id":"current","burst_id":"burst",
        "worker":{"pid":42,"process_start_fingerprint":"windows:123"}})
    write(tmp_path/"supervisor-progress.json",{
        "process_instance_id":"current","burst_id":"burst","sequence":1,
        "observed_at_utc":(now-timedelta(seconds=700)).isoformat()})
    for stamp in ("not-a-time",(now+timedelta(seconds=10)).isoformat()):
        write(tmp_path/"supervisor-generation-heartbeat.json",{
            "process_instance_id":"current","burst_id":"burst",
            "status":"RUNNING_CURRENT","heartbeat_at_utc":stamp})
        assert stale_generation_requires_reclaim(
            tmp_path,"current",now=now,max_age_seconds=60) is False
