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
