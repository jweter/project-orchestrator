import json,pytest
from pathlib import Path
from project_orchestrator.runtime_leases import RuntimeLeasePool

CAP={"native-windows":2}

def test_corrupt_state_is_quarantined_and_reinitialized(tmp_path):
    state=tmp_path/"leases.json"; state.write_text("{broken",encoding="utf-8")
    result=RuntimeLeasePool.recover_invalid_state(CAP,state,fingerprint=lambda p:f"p:{p}",pid_alive=lambda p:False)
    assert result["status"]=="RECOVERED"
    assert Path(result["quarantine"]).read_text(encoding="utf-8")=="{broken"
    assert json.loads(state.read_text(encoding="utf-8"))["leases"]==[]
    audit=state.with_suffix(".json.recovery.jsonl")
    assert "RUNTIME_LEASE_STATE_QUARANTINED" in audit.read_text(encoding="utf-8")

def test_recovery_refuses_to_displace_plausible_live_owner(tmp_path):
    state=tmp_path/"leases.json"
    live={"runtime_id":"unknown","worker_id":"w","owner_pid":77,"owner_fingerprint":"p:77",
          "lease_id":"lease","acquired_at":1,"heartbeat_at":1}
    state.write_text(json.dumps({"schema_version":2,"leases":[live]}),encoding="utf-8")
    with pytest.raises(RuntimeError,match="RUNTIME_LEASE_RECOVERY_LIVE_OWNER"):
        RuntimeLeasePool.recover_invalid_state(CAP,state,fingerprint=lambda p:f"p:{p}",pid_alive=lambda p:True)
    assert state.exists()
    assert not list(tmp_path.glob("*.quarantine-*"))

def test_valid_state_is_not_quarantined(tmp_path):
    state=tmp_path/"leases.json"
    state.write_text(json.dumps({"schema_version":2,"leases":[]}),encoding="utf-8")
    result=RuntimeLeasePool.recover_invalid_state(CAP,state,fingerprint=lambda p:f"p:{p}",pid_alive=lambda p:False)
    assert result["status"]=="VALID"
    assert not list(tmp_path.glob("*.quarantine-*"))
