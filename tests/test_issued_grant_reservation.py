import json
from datetime import UTC, datetime, timedelta
import scripts.autonomous_slice_builder as builder
import scripts.continuous_flow_supervisor as supervisor

SLICE={"capability_id":"cap","slice_id":"slice","repository":"jweter/repo","exact_head":"a"*40,
       "objective":"bounded work","acceptance":["tests pass"]}

def test_write_execution_grant_reserves_issued_before_return(tmp_path,monkeypatch):
    repo_root=tmp_path/"orchestrator"; (repo_root/"engineering").mkdir(parents=True)
    (repo_root/"engineering"/"hands-off-evolution.json").write_text(json.dumps({"products":{"product":{"repositories":["jweter/repo"]}}}),encoding="utf-8")
    state=tmp_path/"state"; state.mkdir()
    (state/"hands-off-activation.json").write_text(json.dumps({"products":{"product":{"repositories":{"jweter/repo":{"head":"a"*40}}}}}),encoding="utf-8")
    monkeypatch.setattr(supervisor,"select_execution_target",lambda *a,**k:("jweter/repo","a"*40))
    monkeypatch.setattr(supervisor,"load_execution_slice",lambda *a,**k:dict(SLICE))
    lock={"path":state/"fake.lock","worker":{"host":"h","pid":1},"lock_id":"lock"}
    monkeypatch.setattr(supervisor,"acquire_grant_issue_lock",lambda *_:lock)
    monkeypatch.setattr(supervisor,"release_grant_issue_lock",lambda *_:None)
    path=supervisor.write_execution_grant(repo_root,state,["product"])
    assert path.parent==state/"hands-off-execution-grants"
    grant=json.loads(path.read_text())
    record=json.loads((state/"hands-off-grant-lifecycle.json").read_text())["grants"][grant["grant_id"]]
    assert record["state"]=="ISSUED"
    assert {k:record["execution_slice"][k] for k in SLICE}==SLICE
    assert record["execution_slice"]["runner_id"]=="aider-ollama"
    assert record["execution_slice"]["execution_runtime"]=="native-windows"

def test_issued_reservation_transitions_to_claimed(tmp_path):
    grant_dir=tmp_path/"hands-off-execution-grants"; grant_dir.mkdir()
    grant=grant_dir/"g1.json"
    grant.write_text(json.dumps({"grant_id":"g1","execution_slice":SLICE}),encoding="utf-8")
    lifecycle=tmp_path/"hands-off-grant-lifecycle.json"
    lifecycle.write_text(json.dumps({"schema_version":1,"grants":{"g1":{
        "state":"ISSUED","issued_at":datetime.now(UTC).isoformat(),
        "authorized_repositories":["jweter/repo"],"expected_sha":"a"*40,
        "execution_slice":SLICE}}}),encoding="utf-8")
    worker={"host":"host","pid":9,"process_start_fingerprint":"fingerprint"}
    assert builder.claim_execution_grant(grant,"jweter/repo","a"*40,worker=worker)
    record=json.loads(lifecycle.read_text())["grants"]["g1"]
    assert record["state"]=="CLAIMED"
    assert record["worker"]==worker

def test_issued_repo_blocks_parallel_different_slice(tmp_path):
    lifecycle=tmp_path/"hands-off-grant-lifecycle.json"
    other=dict(SLICE,slice_id="old")
    lifecycle.write_text(json.dumps({"schema_version":1,"grants":{"g1":{
        "state":"ISSUED","authorized_repositories":["jweter/repo"],"execution_slice":other}}}),encoding="utf-8")
    conflict=supervisor.conflicting_nonterminal_grant(tmp_path,"jweter/repo",SLICE)
    assert conflict["grant_id"]=="g1"
    assert conflict["state"]=="ISSUED"

def test_stale_issued_dead_owner_is_recovered(tmp_path,monkeypatch):
    old=(datetime.now(UTC)-timedelta(seconds=supervisor.ISSUE_LEASE_SECONDS+1)).isoformat()
    lifecycle=tmp_path/"hands-off-grant-lifecycle.json"
    lifecycle.write_text(json.dumps({"schema_version":1,"grants":{"g1":{
        "state":"ISSUED","issued_at":old,"issuer":{"host":"h","pid":9}}}}),encoding="utf-8")
    monkeypatch.setattr(supervisor,"worker_liveness",lambda worker:"DEAD")
    assert supervisor.recover_stale_issued_grants(tmp_path)==["g1"]
    record=json.loads(lifecycle.read_text())["grants"]["g1"]
    assert record["state"]=="ABANDONED"

def test_builder_exit_without_claim_terminalizes_reservation(tmp_path):
    grant_dir=tmp_path/"hands-off-execution-grants"; grant_dir.mkdir()
    grant=grant_dir/"g1.json"; grant.write_text(json.dumps({"grant_id":"g1"}),encoding="utf-8")
    lifecycle=tmp_path/"hands-off-grant-lifecycle.json"
    lifecycle.write_text(json.dumps({"schema_version":1,"grants":{"g1":{"state":"ISSUED"}}}),encoding="utf-8")
    assert supervisor.finalize_unclaimed_grant(grant,2)
    record=json.loads(lifecycle.read_text())["grants"]["g1"]
    assert record["state"]=="ABANDONED"
    assert record["result_status"]=="BUILDER_EXITED_WITHOUT_CLAIM:2"


def test_builder_exit_without_claim_records_preclaim_failure_class(tmp_path):
    grant_dir=tmp_path/"hands-off-execution-grants"; grant_dir.mkdir()
    grant=grant_dir/"g1.json"; grant.write_text(json.dumps({"grant_id":"g1"}),encoding="utf-8")
    lifecycle=tmp_path/"hands-off-grant-lifecycle.json"
    lifecycle.write_text(json.dumps({"schema_version":1,"grants":{"g1":{"state":"ISSUED"}}}),encoding="utf-8")
    builder_state=tmp_path/"builder.json"
    builder_state.write_text(json.dumps({"status":"P1_BUILDER_FAILURE","error_class":"RuntimeError"}),encoding="utf-8")
    assert supervisor.finalize_unclaimed_grant(grant,1,builder_state)
    record=json.loads(lifecycle.read_text())["grants"]["g1"]
    assert record["state"]=="ABANDONED"
    assert record["result_status"]=="BUILDER_EXITED_WITHOUT_CLAIM:1:P1_BUILDER_FAILURE:RuntimeError"
