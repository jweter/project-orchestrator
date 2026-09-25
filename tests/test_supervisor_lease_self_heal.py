import json
from pathlib import Path
import scripts.continuous_flow_supervisor as supervisor

def test_supervisor_self_heals_corrupt_authority(tmp_path,monkeypatch):
    state=tmp_path/"runtime-leases.json"; state.write_text("{broken",encoding="utf-8")
    monkeypatch.setattr(supervisor.RuntimeLeasePool,"_default_pid_alive",staticmethod(lambda pid:False))
    pool,recovery=supervisor.load_runtime_leases_with_recovery(state)
    assert recovery["status"]=="RECOVERED"
    assert pool.snapshot()["leases"]==[]
    assert Path(recovery["quarantine"]).exists()

def test_supervisor_does_not_recover_valid_authority(tmp_path):
    state=tmp_path/"runtime-leases.json"
    state.write_text(json.dumps({"schema_version":2,"leases":[]}),encoding="utf-8")
    pool,recovery=supervisor.load_runtime_leases_with_recovery(state)
    assert recovery is None
    assert pool.snapshot()["leases"]==[]
