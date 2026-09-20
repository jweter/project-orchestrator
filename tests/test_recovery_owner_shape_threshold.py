import json
from datetime import UTC, datetime, timedelta
import scripts.continuous_flow_supervisor as supervisor

def test_locked_issuance_honors_repository_only_recovery_owner(tmp_path,monkeypatch):
    repo_root=tmp_path/"orchestrator"; repo_root.mkdir(); state=tmp_path/"state"; state.mkdir()
    (repo_root/"engineering").mkdir()
    (repo_root/"engineering"/"hands-off-evolution.json").write_text(json.dumps({"products":{"P":{"repositories":["o/repo"]}}}))
    (state/"hands-off-activation.json").write_text(json.dumps({"products":{"P":{"repositories":{"o/repo":"abc"}}}}))
    monkeypatch.setattr(supervisor,"recover_stale_issued_grants",lambda *_: {})
    monkeypatch.setattr(supervisor,"select_execution_target",lambda *_: ("o/repo","abc"))
    monkeypatch.setattr(supervisor,"load_execution_slice",lambda *_: {"capability_id":"c","slice_id":"s","repository":"o/repo","exact_head":"abc"})
    calls={"n":0}
    def conflict(*_):
        calls["n"]+=1
        if calls["n"]==1:
            (state/"hands-off-grant-lifecycle.json").write_text(json.dumps({"schema_version":1,"grants":{"old":{"state":"ABANDONED","repository":"o/repo","recovery_generation":"tok"}}}))
        return None
    monkeypatch.setattr(supervisor,"conflicting_nonterminal_grant",conflict)
    try:
        supervisor.write_execution_grant(repo_root,state,["P"])
        assert False
    except RuntimeError as exc:
        assert str(exc).startswith("EXECUTION_SLICE_ALREADY_OWNED:old:")

def test_dead_recovery_reclaims_at_recovery_lease_threshold(tmp_path,monkeypatch):
    path=tmp_path/"hands-off-grant-lifecycle.json"
    old=(datetime.now(UTC)-timedelta(seconds=supervisor.RECOVERY_GENERATION_LEASE_SECONDS+1)).isoformat()
    record={"state":"ABANDONED","repository":"o/repo","recovery_generation":"old",
            "recovery_generation_started_at":old,"recovery_generation_worker":{"host":"x","pid":7},
            "recovery_version":1}
    path.write_text(json.dumps({"schema_version":1,"grants":{"g":record}}))
    monkeypatch.setattr(supervisor,"worker_liveness",lambda _: "DEAD")
    result=supervisor.begin_recovery_generation(path,"g",dict(record))
    assert result is not None
    assert result[1]["recovery_version"]==2
