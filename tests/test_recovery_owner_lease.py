import json
from datetime import UTC, datetime, timedelta
import scripts.continuous_flow_supervisor as supervisor

def test_dead_stale_recovery_generation_is_reclaimed(tmp_path, monkeypatch):
    path=tmp_path/"hands-off-grant-lifecycle.json"
    old=(datetime.now(UTC)-timedelta(seconds=supervisor.CLAIM_LEASE_SECONDS+10)).isoformat()
    record={"state":"ABANDONED","authorized_repositories":["o/repo"],
            "recovery_generation":"old","recovery_generation_started_at":old,
            "recovery_owner":{"host":"x","pid":7},"recovery_version":1}
    path.write_text(json.dumps({"schema_version":1,"grants":{"g":record}}))
    monkeypatch.setattr(supervisor,"worker_liveness",lambda _: "DEAD")
    result=supervisor.begin_recovery_generation(path,"g",dict(record))
    assert result is not None
    token,reserved=result
    assert token!="old"
    assert reserved["recovery_version"]==2
    assert reserved["recovery_owner"]["pid"]>0

def test_live_or_recent_recovery_generation_is_not_stolen(tmp_path, monkeypatch):
    path=tmp_path/"hands-off-grant-lifecycle.json"
    now=datetime.now(UTC).isoformat()
    record={"state":"ABANDONED","authorized_repositories":["o/repo"],
            "recovery_generation":"live","recovery_generation_started_at":now,
            "recovery_owner":{"host":"x","pid":7},"recovery_version":1}
    path.write_text(json.dumps({"schema_version":1,"grants":{"g":record}}))
    called=[]
    monkeypatch.setattr(supervisor,"worker_liveness",lambda x: called.append(x) or "DEAD")
    assert supervisor.begin_recovery_generation(path,"g",dict(record)) is None
    assert called==[]

def test_issuance_rechecks_recovery_conflict_inside_lifecycle_transaction(tmp_path, monkeypatch):
    repo_root=tmp_path/"orchestrator"; repo_root.mkdir()
    state=tmp_path/"state"; state.mkdir()
    (repo_root/"engineering").mkdir()
    (repo_root/"engineering"/"hands-off-evolution.json").write_text(json.dumps({"products":{"P":{"repositories":["o/repo"]}}}))
    activation={"products":{"P":{"repositories":{"o/repo":"abc"}}}}
    (state/"hands-off-activation.json").write_text(json.dumps(activation))
    monkeypatch.setattr(supervisor,"recover_stale_issued_grants",lambda *_: {})
    monkeypatch.setattr(supervisor,"select_execution_target",lambda *_: ("o/repo","abc"))
    monkeypatch.setattr(supervisor,"load_execution_slice",lambda *_: {"capability_id":"c","slice_id":"s","repository":"o/repo","exact_head":"abc"})
    calls={"n":0}
    def conflict(*_):
        calls["n"]+=1
        if calls["n"]==1:
            lifecycle=state/"hands-off-grant-lifecycle.json"
            lifecycle.write_text(json.dumps({"schema_version":1,"grants":{"old":{"state":"ABANDONED","authorized_repositories":["o/repo"],"recovery_generation":"tok"}}}))
        return None
    monkeypatch.setattr(supervisor,"conflicting_nonterminal_grant",conflict)
    try:
        supervisor.write_execution_grant(repo_root,state,["P"])
        assert False,"issuance should fail closed"
    except RuntimeError as exc:
        assert str(exc).startswith("EXECUTION_SLICE_ALREADY_OWNED:old:")
