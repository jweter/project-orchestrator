import json
import scripts.continuous_flow_supervisor as supervisor

def test_grant_busy_writes_worker_local_state(tmp_path,monkeypatch):
    monkeypatch.setattr(supervisor,"write_execution_grant",
                        lambda *a,**k: (_ for _ in ()).throw(RuntimeError("GRANT_ISSUANCE_BUSY")))
    monkeypatch.setattr(supervisor.time,"sleep",lambda *_:None)
    rc,inv=supervisor.run_builder(tmp_path,tmp_path,tmp_path,["rocksmith"],worker_id="rocksmith-worker-1")
    assert rc==0 and inv is None
    local=tmp_path/"worker-state"/"rocksmith-worker-1"/"builder.json"
    assert json.loads(local.read_text())["status"]=="OWNERSHIP_HELD"
    assert not (tmp_path/"builder.json").exists()

def test_owned_slice_writes_worker_local_state(tmp_path,monkeypatch):
    monkeypatch.setattr(supervisor,"write_execution_grant",
                        lambda *a,**k: (_ for _ in ()).throw(RuntimeError("EXECUTION_SLICE_ALREADY_OWNED:g:CLAIMED")))
    rc,inv=supervisor.run_builder(tmp_path,tmp_path,tmp_path,["everward"],worker_id="everward-worker-1")
    assert rc==0 and inv is None
    local=tmp_path/"worker-state"/"everward-worker-1"/"builder.json"
    assert json.loads(local.read_text())["status"]=="OWNERSHIP_HELD"
    assert not (tmp_path/"builder.json").exists()
