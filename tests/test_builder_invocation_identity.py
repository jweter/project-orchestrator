import json
import sys
from pathlib import Path

import scripts.autonomous_slice_builder as builder
import scripts.continuous_flow_supervisor as supervisor


def test_builder_health_rejects_stale_invocation_identity(tmp_path):
    (tmp_path/"builder.json").write_text(
        json.dumps({"status":"PR_OPEN","invocation_id":"old"}),encoding="utf-8"
    )
    assert supervisor.builder_health(tmp_path,0,"new")==(
        False,"STALE_BUILDER_STATE"
    )
    assert supervisor.builder_health(tmp_path,0,"old")==(
        True,"PR_OPEN"
    )


def test_run_builder_passes_and_returns_same_invocation_id(tmp_path,monkeypatch):
    state=tmp_path/"state"; state.mkdir()
    grant=state/"grant.json"; grant.write_text(json.dumps({"schema_version":5,"products":{"voicestudio":{"repositories":{"jweter/VoiceStudio":"abc123"}}},"execution_slice":{"repository":"jweter/VoiceStudio","runner_id":"aider-ollama","execution_runtime":"native-windows","runtime_lease_id":"lease-test"}}),encoding="utf-8")
    (state/"dispatch-decision.json").write_text(json.dumps({"schema_version":1,"recorded_at_utc":"2026-09-26T00:56:58+00:00","decisions":[{"repository":"stale/repo"}]}),encoding="utf-8")
    captured={}
    class Result:
        returncode=0
        stdout=""
        stderr=""
    def fake_run(cmd,**kwargs):
        captured["cmd"]=cmd
        return Result()
    monkeypatch.setattr(supervisor,"write_execution_grant",lambda *a,**k:grant)
    monkeypatch.setattr(supervisor.subprocess,"run",fake_run)
    monkeypatch.setattr(supervisor,"finalize_unclaimed_grant",lambda *a,**k:False)
    rc,invocation_id=supervisor.run_builder(tmp_path,tmp_path,state,["product"])
    assert rc==0
    idx=captured["cmd"].index("--invocation-id")
    assert captured["cmd"][idx+1]==invocation_id
    assert invocation_id
    dispatch=json.loads((state/"dispatch-decision.json").read_text(encoding="utf-8"))
    assert dispatch["invocation_id"]==invocation_id
    assert dispatch["grant_path"]==str(grant)
    assert dispatch["selected_product"]=="voicestudio"
    assert dispatch["selected_repository"]=="jweter/VoiceStudio"
    assert dispatch["recorded_at_utc"]!="2026-09-26T00:56:58+00:00"


def test_runtime_fence_uses_granted_worker_identity(tmp_path,monkeypatch):
    # Test only: simulate an authorized owner state to reach downstream logic.
    # Live consent enforcement remains unchanged and is tested separately.
    monkeypatch.setattr(builder,"owner_control_status",lambda _root:builder.AUTHORIZED)
    monkeypatch.setattr(builder,"ACTIVE_RUNTIME_FENCE",None)
    monkeypatch.setattr(builder,"ACTIVE_RESOURCE_FENCES",[])
    monkeypatch.setattr(builder,"ACTIVE_RUNTIME_ID","native-windows")
    from project_orchestrator.runtime_leases import RuntimeLeasePool, DEFAULT_RUNTIME_CAPACITIES
    lease_state=tmp_path/"leases.json"
    pool=RuntimeLeasePool(DEFAULT_RUNTIME_CAPACITIES,lease_state)
    lease=pool.acquire("native-windows","rocksmith-worker-1")
    grant=tmp_path/"grant.json"
    grant.write_text(json.dumps({"schema_version":5,"worker_id":"rocksmith-worker-1",
        "execution_slice":{"runner_id":"aider-ollama","execution_runtime":"native-windows"}}),encoding="utf-8")
    state=tmp_path/"builder.json"
    monkeypatch.setattr(builder,"granted_repositories",lambda *a,**k:{})
    monkeypatch.setattr(sys,"argv",[
        "autonomous_slice_builder.py","--workspace-root",str(tmp_path),"--state",str(state),
        "--execution-grant",str(grant),"--invocation-id","different-invocation",
        "--runner-id","aider-ollama","--runtime-id","native-windows",
        "--runtime-lease-id",lease.lease_id,"--runtime-lease-state",str(lease_state),
    ])
    assert builder.main()==0
    assert json.loads(state.read_text())["status"]=="NO_ELIGIBLE_REPOSITORY"


def test_early_builder_return_overwrites_prior_state_with_current_invocation(tmp_path,monkeypatch):
    state=tmp_path/"builder.json"
    state.write_text(json.dumps({"status":"PR_OPEN","invocation_id":"prior"}),encoding="utf-8")
    grant=tmp_path/"grant.json"; grant.write_text(json.dumps({"schema_version":5,"worker_id":"knowledge-engine-worker-1","execution_slice":{"runner_id":"aider-ollama","execution_runtime":"native-windows","runtime_lease_id":"lease-test"}}),encoding="utf-8")
    monkeypatch.setattr(builder,"granted_repositories",lambda *a,**k:{"jweter/repo":"head"})
    captured={}
    def fake_build_once(*args,**kwargs):
        captured["worker_id"]=args[-1]
        return {"status":"USER_ACTIVE","idle_seconds":0}
    monkeypatch.setattr(builder,"build_once",fake_build_once)
    monkeypatch.setattr(builder,"finish_grant_lifecycle",lambda *a,**k:None)
    monkeypatch.setattr(sys,"argv",[
        "autonomous_slice_builder.py","--workspace-root",str(tmp_path),
        "--state",str(state),"--execution-grant",str(grant),
        "--invocation-id","current-invocation","--runner-id","aider-ollama","--runtime-id","native-windows",
    ])
    assert builder.main()==0
    payload=json.loads(state.read_text())
    assert payload["status"]=="USER_ACTIVE"
    assert payload["invocation_id"]=="current-invocation"
    assert captured["worker_id"]=="knowledge-engine-worker-1"


def test_builder_exception_persists_current_invocation(tmp_path,monkeypatch):
    state=tmp_path/"builder.json"
    grant=tmp_path/"grant.json"; grant.write_text(json.dumps({"schema_version":5,"execution_slice":{"runner_id":"aider-ollama","execution_runtime":"native-windows","runtime_lease_id":"lease-test"}}),encoding="utf-8")
    monkeypatch.setattr(builder,"granted_repositories",lambda *a,**k:{"jweter/repo":"head"})
    monkeypatch.setattr(builder,"build_once",lambda *a,**k:(_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(sys,"argv",[
        "autonomous_slice_builder.py","--workspace-root",str(tmp_path),
        "--state",str(state),"--execution-grant",str(grant),
        "--invocation-id","exception-invocation","--runner-id","aider-ollama","--runtime-id","native-windows",
    ])
    assert builder.main()==1
    payload=json.loads(state.read_text())
    assert payload["status"]=="P1_BUILDER_FAILURE"
    assert payload["invocation_id"]=="exception-invocation"



def test_builder_exception_finalizes_claimed_grant_immediately(tmp_path,monkeypatch):
    state=tmp_path/"builder.json"
    grants=tmp_path/"hands-off-execution-grants"; grants.mkdir()
    grant=grants/"grant-1.json"
    grant.write_text(json.dumps({"grant_id":"grant-1","schema_version":5,"execution_slice":{"runner_id":"aider-ollama","execution_runtime":"native-windows"}}),encoding="utf-8")
    lifecycle=tmp_path/"hands-off-grant-lifecycle.json"
    lifecycle.write_text(json.dumps({"schema_version":1,"grants":{"grant-1":{"state":"CLAIMED","claimed_at":"2026-09-29T10:00:00+00:00"}}}),encoding="utf-8")
    monkeypatch.setattr(builder,"granted_repositories",lambda *a,**k:{"jweter/repo":"head"})
    monkeypatch.setattr(builder,"build_once",lambda *a,**k:(_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(sys,"argv",["autonomous_slice_builder.py","--workspace-root",str(tmp_path),"--state",str(state),"--execution-grant",str(grant),"--invocation-id","failure-finalizes-grant","--runner-id","aider-ollama","--runtime-id","native-windows"])
    assert builder.main()==1
    record=json.loads(lifecycle.read_text())["grants"]["grant-1"]
    assert record["state"]=="FAILED"
    assert record["result_status"]=="P1_BUILDER_FAILURE"
    assert record["finished_at"]
