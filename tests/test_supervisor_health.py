from datetime import UTC, datetime, timedelta
from project_orchestrator.supervisor_health import evaluate_supervisor_health, fence_generation, generation_is_fenced, verify_reclaim_identity

NOW=datetime(2026,10,3,1,0,tzinfo=UTC)

def process():
    return {"process_instance_id":"gen","burst_id":"burst","worker":{
        "pid":42724,"process_start_fingerprint":"windows:123"}}

def heartbeat(age=10,status="RUNNING_CURRENT"):
    return {"process_instance_id":"gen","burst_id":"burst","status":status,
            "heartbeat_at_utc":(NOW-timedelta(seconds=age)).isoformat()}

def progress(age=10):
    return {"process_instance_id":"gen","burst_id":"burst","sequence":42,
            "observed_at_utc":(NOW-timedelta(seconds=age)).isoformat()}

def test_healthy_requires_fresh_heartbeat_and_progress():
    result=evaluate_supervisor_health(process=process(),heartbeat=heartbeat(),progress=progress(),now=NOW)
    assert result["status"]=="HEALTHY"
    assert result["reclaimable"] is False

def test_publication_blocked_is_degraded_not_reclaimable_when_progressing():
    result=evaluate_supervisor_health(process=process(),heartbeat=heartbeat(status="STATE_PUBLICATION_BLOCKED"),progress=progress(),now=NOW)
    assert result["status"]=="DEGRADED_PUBLICATION_BLOCKED"
    assert result["reclaimable"] is False

def test_alive_but_stalled_generation_is_reclaimable():
    result=evaluate_supervisor_health(process=process(),heartbeat=heartbeat(age=121),progress=progress(age=601),now=NOW)
    assert result["status"]=="SUPERVISOR_STALLED"
    assert result["reclaimable"] is True
    assert result["pid"]==42724

def test_stale_heartbeat_with_recent_progress_fails_closed():
    result=evaluate_supervisor_health(process=process(),heartbeat=heartbeat(age=121),progress=progress(age=20),now=NOW)
    assert result["status"]=="HEARTBEAT_STALE_PROGRESS_RECENT"
    assert result["reclaimable"] is False

def test_identity_mismatch_is_never_reclaimable():
    hb=heartbeat(); hb["process_instance_id"]="other"
    result=evaluate_supervisor_health(process=process(),heartbeat=hb,progress=progress(),now=NOW)
    assert result["status"]=="HEARTBEAT_IDENTITY_MISMATCH"
    assert result["reclaimable"] is False


def test_wrong_progress_burst_does_not_authorize_reclaim():
    pg=progress(age=10); pg["burst_id"]="old-burst"
    result=evaluate_supervisor_health(process=process(),heartbeat=heartbeat(age=121),progress=pg,now=NOW)
    assert result["status"]=="PROGRESS_UNREADABLE"
    assert result["reclaimable"] is False


def test_future_heartbeat_fails_closed_on_clock_skew():
    hb=heartbeat(); hb["heartbeat_at_utc"]=(NOW+timedelta(seconds=1)).isoformat()
    result=evaluate_supervisor_health(process=process(),heartbeat=hb,progress=progress(),now=NOW)
    assert result["status"]=="CLOCK_SKEW"
    assert result["reclaimable"] is False


def test_missing_process_fingerprint_cannot_be_reclaimed():
    proc=process(); proc["worker"].pop("process_start_fingerprint")
    result=evaluate_supervisor_health(process=proc,heartbeat=heartbeat(age=999),progress=progress(age=999),now=NOW)
    assert result["status"]=="IDENTITY_UNPROVEN"
    assert result["reclaimable"] is False


def test_reclaim_identity_reproves_exact_process_fingerprint():
    health=evaluate_supervisor_health(process=process(),heartbeat=heartbeat(age=121),progress=progress(age=601),now=NOW)
    result=verify_reclaim_identity(health,fingerprint_fn=lambda pid:"windows:123")
    assert result["status"]=="RECLAIM_IDENTITY_VERIFIED"
    assert result["verified"] is True
    assert result["pid"]==42724


def test_reclaim_identity_rejects_pid_reuse_or_process_change():
    health=evaluate_supervisor_health(process=process(),heartbeat=heartbeat(age=121),progress=progress(age=601),now=NOW)
    result=verify_reclaim_identity(health,fingerprint_fn=lambda pid:"windows:999")
    assert result["status"]=="PROCESS_IDENTITY_CHANGED"
    assert result["verified"] is False


def test_reclaim_identity_refuses_healthy_generation_even_if_process_matches():
    health=evaluate_supervisor_health(process=process(),heartbeat=heartbeat(),progress=progress(),now=NOW)
    result=verify_reclaim_identity(health,fingerprint_fn=lambda pid:"windows:123")
    assert result=={"status":"RECLAIM_NOT_AUTHORIZED","verified":False}


def test_verified_stalled_generation_can_be_fenced_without_fencing_successor(tmp_path):
    health=evaluate_supervisor_health(process=process(),heartbeat=heartbeat(age=121),progress=progress(age=601),now=NOW)
    verified=verify_reclaim_identity(health,fingerprint_fn=lambda pid:"windows:123")
    fence=fence_generation(tmp_path,verified,now=NOW)
    assert fence["status"]=="GENERATION_FENCED"
    assert generation_is_fenced(tmp_path,process_instance_id="gen",burst_id="burst")
    assert not generation_is_fenced(tmp_path,process_instance_id="successor",burst_id="burst-2")


def test_unverified_generation_cannot_create_fence(tmp_path):
    try:
        fence_generation(tmp_path,{"status":"PROCESS_IDENTITY_CHANGED","verified":False},now=NOW)
    except ValueError as exc:
        assert "not verified" in str(exc)
    else:
        raise AssertionError("unverified generation was fenced")


def test_retire_verified_generation_requires_exact_identity_and_proves_exit():
    from types import SimpleNamespace
    from project_orchestrator.supervisor_health import retire_verified_generation
    calls=[]
    fingerprints=iter(["windows:123",None])
    verified={"status":"RECLAIM_IDENTITY_VERIFIED","verified":True,"pid":42,
              "process_start_fingerprint":"windows:123"}
    result=retire_verified_generation(
        verified,
        run_fn=lambda cmd,**kwargs:(calls.append(cmd) or SimpleNamespace(returncode=0)),
        fingerprint_fn=lambda pid:next(fingerprints))
    assert result["status"]=="PROCESS_RETIRED"
    assert result["retired"] is True
    assert calls==[["taskkill","/PID","42","/T","/F"]]


def test_retire_verified_generation_refuses_changed_identity():
    from project_orchestrator.supervisor_health import retire_verified_generation
    verified={"status":"RECLAIM_IDENTITY_VERIFIED","verified":True,"pid":42,
              "process_start_fingerprint":"windows:123"}
    result=retire_verified_generation(
        verified,run_fn=lambda *_a,**_k:(_ for _ in ()).throw(AssertionError("must not kill")),
        fingerprint_fn=lambda pid:"windows:999")
    assert result["status"]=="PROCESS_IDENTITY_CHANGED"
    assert result["retired"] is False
