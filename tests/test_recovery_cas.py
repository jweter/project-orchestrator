import json
import scripts.continuous_flow_supervisor as supervisor

def test_recovery_cas_preserves_concurrent_ownership_transition(tmp_path):
    path=tmp_path/"hands-off-grant-lifecycle.json"
    original={"state":"ABANDONED","recovery_state":"RECOVERING","execution_stage":"COMMITTED",
              "head_sha":"abc","branch":"actor/a"}
    path.write_text(json.dumps({"schema_version":1,"grants":{"g":original}}))
    stale=dict(original); stale["recovery_state"]="RECOVERED"
    live=json.loads(path.read_text()); live["grants"]["g"]["state"]="CLAIMED"
    supervisor.atomic(path,live)
    assert supervisor.commit_recovery_record(path,"g",original,stale) is False
    assert json.loads(path.read_text())["grants"]["g"]["state"]=="CLAIMED"

def test_recovery_cas_commits_when_identity_is_unchanged(tmp_path):
    path=tmp_path/"hands-off-grant-lifecycle.json"
    original={"state":"ABANDONED","recovery_state":"RECOVERING","execution_stage":"COMMITTED",
              "head_sha":"abc","branch":"actor/a"}
    path.write_text(json.dumps({"schema_version":1,"grants":{"g":original}}))
    updated=dict(original); updated["recovery_state"]="RECOVERED"
    assert supervisor.commit_recovery_record(path,"g",original,updated) is True
    assert json.loads(path.read_text())["grants"]["g"]["recovery_state"]=="RECOVERED"

def test_reconcile_fails_closed_when_external_work_races_new_claim(tmp_path, monkeypatch):
    state=tmp_path/"state"; state.mkdir(); work=tmp_path/"work"; work.mkdir()
    ws=work/"repo"; ws.mkdir()
    path=state/"hands-off-grant-lifecycle.json"
    original={"state":"ABANDONED","recovery_state":"RECOVERING","execution_stage":"COMMITTED",
              "head_sha":"abc","branch":"actor/a","repository":"o/repo","recovery_attempts":0}
    path.write_text(json.dumps({"schema_version":1,"grants":{"g":original}}))
    calls={"n":0}
    class R:
        returncode=0; stdout=""
    def run(cmd,**kwargs):
        calls["n"]+=1
        if calls["n"]==1:
            live=json.loads(path.read_text()); live["grants"]["g"]["state"]="CLAIMED"; live["grants"]["g"]["worker"]={"pid":7}
            supervisor.atomic(path,live)
            R.stdout="actor/a\\n"
        else:
            R.stdout="abc\\n"
        return R()
    monkeypatch.setattr(supervisor.subprocess,"run",run)
    monkeypatch.setattr(supervisor,"resume_orphan_branch",lambda *a,**k:"RECOVERY_PR_OPENED")
    out=supervisor.reconcile_abandoned_claims(work,state,["g"])
    assert out["g"]=="RECOVERY_CONCURRENT_STATE_CHANGE"
    live=json.loads(path.read_text())["grants"]["g"]
    assert live["state"]=="CLAIMED"
    assert live["worker"]=={"pid":7}
