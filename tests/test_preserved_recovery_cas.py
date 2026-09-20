import json
import scripts.continuous_flow_supervisor as supervisor

def test_preserved_recovery_rejects_stale_snapshot_after_concurrent_claim(tmp_path, monkeypatch):
    state=tmp_path/"state"; state.mkdir(); work=tmp_path/"work"; work.mkdir(); ws=work/"repo"; ws.mkdir()
    path=state/"hands-off-grant-lifecycle.json"
    original={"state":"QUARANTINED","recovery_state":"QUARANTINED",
              "recovery_quarantine_reason":"UNCOMMITTED_WORK_REQUIRES_PRESERVATION",
              "repository":"o/repo","execution_stage":"BRANCH_CREATED","branch":"actor/a","head_sha":"abc"}
    path.write_text(json.dumps({"schema_version":1,"grants":{"g":original}}))
    def revalidate(*_):
        live=json.loads(path.read_text()); live["grants"]["g"]["state"]="CLAIMED"; live["grants"]["g"]["worker"]={"pid":9}
        supervisor.atomic(path,live)
        return "PRESERVED_WORK_EXACT"
    monkeypatch.setattr(supervisor,"revalidate_preserved_work",revalidate)
    monkeypatch.setattr(supervisor,"validate_preserved_work",lambda *_:"PRESERVED_VALIDATION_FAILED")
    out=supervisor.recheck_preserved_work_quarantines(work,state)
    assert out["g"]=="PRESERVED_RECOVERY_CONCURRENT_STATE_CHANGE"
    live=json.loads(path.read_text())["grants"]["g"]
    assert live["state"]=="CLAIMED"
    assert live["worker"]=={"pid":9}
