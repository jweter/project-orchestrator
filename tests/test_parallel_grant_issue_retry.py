import json
import scripts.continuous_flow_supervisor as supervisor

def test_run_builder_retries_transient_grant_issue_contention(tmp_path, monkeypatch):
    state=tmp_path/"state"; state.mkdir()
    attempts={"count":0}
    grant=state/"hands-off-execution-grants"/"g.json"
    grant.parent.mkdir()
    grant.write_text(json.dumps({"schema_version":5,"grant_id":"g","execution_slice":{"runner_id":"aider-ollama","execution_runtime":"native-windows","runtime_lease_id":"lease-test"}}),encoding="utf-8")
    def issue(*_args,**_kwargs):
        attempts["count"]+=1
        if attempts["count"]<3:
            raise RuntimeError("GRANT_ISSUANCE_BUSY")
        return grant
    monkeypatch.setattr(supervisor,"write_execution_grant",issue)
    monkeypatch.setattr(supervisor.time,"sleep",lambda *_:None)
    monkeypatch.setattr(supervisor.subprocess,"run",lambda *a,**k:type("R",(),{"returncode":0,"stdout":"","stderr":""})())
    monkeypatch.setattr(supervisor,"finalize_unclaimed_grant",lambda *_:True)
    rc,_=supervisor.run_builder(tmp_path,tmp_path,state,["product"])
    assert rc==0
    assert attempts["count"]==3

