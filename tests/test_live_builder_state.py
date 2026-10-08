import json
import subprocess

import scripts.blocker_buddy as buddy
import scripts.continuous_flow_supervisor as supervisor


def test_run_builder_replaces_stale_worker_failure_before_child_launch(tmp_path,monkeypatch):
    repo_root=tmp_path/"repo"; repo_root.mkdir()
    workspace=tmp_path/"workspace"; workspace.mkdir()
    state=tmp_path/"state"; state.mkdir()
    worker_dir=state/"worker-state"/"everward-worker-1"
    worker_dir.mkdir(parents=True)
    builder_state=worker_dir/"builder.json"
    builder_output=worker_dir/"builder-last-output.txt"
    builder_state.write_text(json.dumps({
        "status":"P1_BUILDER_FAILURE","invocation_id":"old","error":"workspace dirty"
    }),encoding="utf-8")
    builder_output.write_text("old failure output",encoding="utf-8")
    grants=state/"hands-off-execution-grants"; grants.mkdir()
    grant=grants/"g.json"
    grant.write_text(json.dumps({
        "grant_id":"g-live",
        "execution_slice":{
            "runner_id":"claude-code","execution_runtime":"native-windows",
            "runtime_lease_id":"lease-1","resource_lease_ids":{}
        }
    }),encoding="utf-8")
    monkeypatch.setattr(supervisor,"write_execution_grant",lambda *a,**k:grant)
    monkeypatch.setattr(supervisor,"finalize_unclaimed_grant",lambda *a,**k:True)
    observed={}
    def fake_run(cmd,**kwargs):
        observed.update(json.loads(builder_state.read_text(encoding="utf-8")))
        observed["output"]=builder_output.read_text(encoding="utf-8")
        return subprocess.CompletedProcess(cmd,0,"{}","")
    monkeypatch.setattr(supervisor.subprocess,"run",fake_run)

    rc,invocation_id=supervisor.run_builder(
        repo_root,workspace,state,["everward"],worker_id="everward-worker-1"
    )

    assert rc==0
    assert observed["status"]=="BUILDER_RUNNING"
    assert observed["invocation_id"]==invocation_id
    assert observed["grant_id"]=="g-live"
    assert observed["worker_id"]=="everward-worker-1"
    assert observed["runner_id"]=="claude-code"
    assert observed["runtime_id"]=="native-windows"
    assert "error" not in observed
    assert observed["output"]==""


def test_blocker_buddy_treats_live_builder_as_working_even_if_liveness_snapshot_is_stale():
    result=buddy.diagnose({"status":"BUILDER_RUNNING"},{"status":"ELIGIBLE_IDLE"})
    assert result["status"]=="WORKING"
    assert "No intervention" in result["action"]
