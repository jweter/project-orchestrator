import json,pytest
from project_orchestrator.runtime_leases import RuntimeLeasePool

CAP={"native-windows":2}

@pytest.mark.parametrize("content",["{broken","[]",json.dumps({"schema_version":99,"leases":[]}),json.dumps({"schema_version":2})])
def test_existing_invalid_authority_state_fails_closed(tmp_path,content):
    state=tmp_path/"leases.json"; state.write_text(content,encoding="utf-8")
    with pytest.raises(RuntimeError,match="RUNTIME_LEASE_STATE_INVALID"):
        RuntimeLeasePool(CAP,state,lambda pid:f"p:{pid}",lambda pid:True)

def test_malformed_lease_fails_closed(tmp_path):
    state=tmp_path/"leases.json"
    state.write_text(json.dumps({"schema_version":2,"leases":[{"runtime_id":"native-windows"}]}),encoding="utf-8")
    with pytest.raises(RuntimeError,match="RUNTIME_LEASE_STATE_INVALID"):
        RuntimeLeasePool(CAP,state,lambda pid:f"p:{pid}",lambda pid:True)

def test_unknown_runtime_fails_closed(tmp_path):
    state=tmp_path/"leases.json"
    lease={"runtime_id":"unknown","worker_id":"w","owner_pid":7,"owner_fingerprint":"p:7","lease_id":"l","acquired_at":1,"heartbeat_at":1}
    state.write_text(json.dumps({"schema_version":2,"leases":[lease]}),encoding="utf-8")
    with pytest.raises(RuntimeError,match="RUNTIME_LEASE_STATE_INVALID"):
        RuntimeLeasePool(CAP,state,lambda pid:f"p:{pid}",lambda pid:True)

def test_duplicate_worker_fails_closed(tmp_path):
    state=tmp_path/"leases.json"
    lease={"runtime_id":"native-windows","worker_id":"w","owner_pid":7,"owner_fingerprint":"p:7","lease_id":"l","acquired_at":1,"heartbeat_at":1}
    state.write_text(json.dumps({"schema_version":2,"leases":[lease,dict(lease,lease_id="l2")]}),encoding="utf-8")
    with pytest.raises(RuntimeError,match="RUNTIME_LEASE_STATE_INVALID"):
        RuntimeLeasePool(CAP,state,lambda pid:f"p:{pid}",lambda pid:True)
