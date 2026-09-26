import json
from datetime import UTC, datetime, timedelta
import scripts.continuous_flow_supervisor as supervisor

def _state(path, record):
    path.write_text(json.dumps({"schema_version":1,"grants":{"g":record}}))

def _record(started, **extra):
    base={"state":"ABANDONED","recovery_state":"RECOVERING",
          "recovery_generation":"tok","recovery_generation_started_at":started,
          "recovery_generation_worker":{"host":"h","pid":1,"process_start_fingerprint":"p"},
          "authorized_repositories":["o/repo"]}
    base.update(extra)
    return base

def test_dead_recovery_generation_owner_is_released(tmp_path):
    path=tmp_path/"hands-off-grant-lifecycle.json"
    old=(datetime.now(UTC)-timedelta(seconds=supervisor.RECOVERY_GENERATION_LEASE_SECONDS+10)).isoformat()
    execution_slice={"capability_id":"c","slice_id":"s","repository":"o/repo","exact_head":"abc"}
    _state(path,_record(old,execution_slice=execution_slice))
    assert supervisor.conflicting_nonterminal_grant(tmp_path,"o/repo",execution_slice) is not None
    released=supervisor.recover_stale_recovery_generations(tmp_path,liveness_probe=lambda _: "DEAD")
    assert released==["g"]
    record=json.loads(path.read_text())["grants"]["g"]
    assert "recovery_generation" not in record
    assert record["recovery_generation_result"]=="RECOVERY_GENERATION_OWNER_DEAD"
    assert supervisor.conflicting_nonterminal_grant(tmp_path,"o/repo",execution_slice) is None

def test_live_recovery_generation_owner_keeps_fence(tmp_path):
    path=tmp_path/"hands-off-grant-lifecycle.json"
    old=(datetime.now(UTC)-timedelta(seconds=supervisor.RECOVERY_GENERATION_LEASE_SECONDS+10)).isoformat()
    _state(path,_record(old))
    assert supervisor.recover_stale_recovery_generations(tmp_path,liveness_probe=lambda _: "ALIVE")==[]
    record=json.loads(path.read_text())["grants"]["g"]
    assert record["recovery_generation"]=="tok"
    assert record["recovery_generation_result"]=="RECOVERY_GENERATION_OWNER_ALIVE"

def test_unknown_recovery_generation_owner_fails_closed(tmp_path):
    path=tmp_path/"hands-off-grant-lifecycle.json"
    old=(datetime.now(UTC)-timedelta(seconds=supervisor.RECOVERY_GENERATION_LEASE_SECONDS+10)).isoformat()
    _state(path,_record(old))
    assert supervisor.recover_stale_recovery_generations(tmp_path,liveness_probe=lambda _: "UNKNOWN")==[]
    record=json.loads(path.read_text())["grants"]["g"]
    assert record["recovery_generation"]=="tok"
    assert record["recovery_generation_unknown_checks"]==1
    assert record["recovery_generation_result"]=="RECOVERY_GENERATION_OWNER_UNKNOWN"

def test_recent_generation_does_not_probe_owner(tmp_path):
    path=tmp_path/"hands-off-grant-lifecycle.json"
    _state(path,_record(datetime.now(UTC).isoformat()))
    called=[]
    assert supervisor.recover_stale_recovery_generations(tmp_path,liveness_probe=lambda x: called.append(x) or "DEAD")==[]
    assert called==[]
    assert json.loads(path.read_text())["grants"]["g"]["recovery_generation"]=="tok"

def test_invalid_generation_timestamp_fails_closed(tmp_path):
    path=tmp_path/"hands-off-grant-lifecycle.json"
    _state(path,_record("not-a-time"))
    assert supervisor.recover_stale_recovery_generations(tmp_path,liveness_probe=lambda _: "DEAD")==[]
    record=json.loads(path.read_text())["grants"]["g"]
    assert record["recovery_generation"]=="tok"
    assert record["recovery_generation_result"]=="RECOVERY_GENERATION_TIMESTAMP_INVALID"
    assert record["recovery_state"]=="QUARANTINED"
    assert record["recovery_quarantine_reason"]=="RECOVERY_GENERATION_TIMESTAMP_INVALID"

def test_repeated_unknown_generation_owner_becomes_explicit_quarantine(tmp_path):
    path=tmp_path/"hands-off-grant-lifecycle.json"
    old=(datetime.now(UTC)-timedelta(seconds=supervisor.RECOVERY_GENERATION_LEASE_SECONDS+10)).isoformat()
    _state(path,_record(old))
    for _ in range(supervisor.UNKNOWN_LIVENESS_MAX_CHECKS):
        assert supervisor.recover_stale_recovery_generations(tmp_path,liveness_probe=lambda _: "UNKNOWN")==[]
    record=json.loads(path.read_text())["grants"]["g"]
    assert record["recovery_generation"]=="tok"
    assert record["recovery_generation_unknown_checks"]==supervisor.UNKNOWN_LIVENESS_MAX_CHECKS
    assert record["recovery_state"]=="QUARANTINED"
    assert record["recovery_quarantine_reason"]=="RECOVERY_GENERATION_OWNER_UNKNOWN"
