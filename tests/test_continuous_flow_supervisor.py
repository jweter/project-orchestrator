from scripts.continuous_flow_supervisor import atomic
import scripts.continuous_flow_supervisor as supervisor
def test_atomic_supervisor_state(tmp_path):
    path=tmp_path/"state.json"; atomic(path,{"status":"HEALTHY"})
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert '"status": "HEALTHY"' in path.read_text(encoding="utf-8")


def test_supervisor_state_uses_extended_replace_budget():
    assert supervisor.SUPERVISOR_STATE_REPLACE_ATTEMPTS == 60


def test_supervisor_publication_lock_keeps_generation_alive(tmp_path, monkeypatch):
    calls = []
    def fake_atomic(path, payload, **kwargs):
        calls.append((path.name, payload, kwargs))
        if path.name == "supervisor.json":
            raise PermissionError(5, "Access is denied")
    monkeypatch.setattr(supervisor, "atomic", fake_atomic)
    published = supervisor.publish_supervisor_snapshot(
        tmp_path, {"status": "HEALTHY"}, process_instance_id="generation-1",
        burst_id="burst-1", revision="abc123",
    )
    assert published is False
    heartbeat = next(payload for name, payload, _ in calls if name == "supervisor-generation-heartbeat.json")
    assert heartbeat["status"] == "STATE_PUBLICATION_BLOCKED"
    assert heartbeat["process_instance_id"] == "generation-1"
    blocked = next(payload for name, payload, _ in calls if name == "supervisor-publication-blocked.json")
    assert blocked["status"] == "STATE_PUBLICATION_BLOCKED"
    supervisor_call = next(kwargs for name, _, kwargs in calls if name == "supervisor.json")
    assert supervisor_call["replace_attempts"] == supervisor.SUPERVISOR_STATE_REPLACE_ATTEMPTS


def test_linear_reconciliation_is_optional(tmp_path):
    from scripts.continuous_flow_supervisor import run_linear_reconciliation
    assert run_linear_reconciliation(tmp_path,tmp_path/"state") is None


def test_builder_health_detects_semantic_failure_even_with_zero_exit(tmp_path):
    import json
    from scripts.continuous_flow_supervisor import builder_health
    (tmp_path/"builder.json").write_text(json.dumps({"status":"P1_BUILDER_FAILURE"}),encoding="utf-8")
    assert builder_health(tmp_path,0)==(False,"P1_BUILDER_FAILURE")

def test_builder_health_accepts_user_active_idle(tmp_path):
    import json
    from scripts.continuous_flow_supervisor import builder_health
    (tmp_path/"builder.json").write_text(json.dumps({"status":"USER_ACTIVE"}),encoding="utf-8")
    assert builder_health(tmp_path,0)==(True,"USER_ACTIVE")

def test_merge_watcher_advancement_detects_merge(tmp_path):
    import json
    import scripts.continuous_flow_supervisor as flow
    original=flow.reconcile_merge_feedback
    flow.reconcile_merge_feedback=lambda root,merged: []
    merge_watcher_advanced=flow.merge_watcher_advanced
    (tmp_path/"green-pr-watcher.json").write_text(
        json.dumps({"results":[{"repository":"r","pr":1,"merged":True}]}),encoding="utf-8")
    assert merge_watcher_advanced(tmp_path) is True
    flow.reconcile_merge_feedback=original

def test_merge_watcher_does_not_advance_on_promotion_only(tmp_path):
    import json
    from scripts.continuous_flow_supervisor import merge_watcher_advanced
    (tmp_path/"green-pr-watcher.json").write_text(
        json.dumps({"results":[{"repository":"r","pr":1,"promoted":True}]}),encoding="utf-8")
    assert merge_watcher_advanced(tmp_path) is False

def test_merge_event_is_consumed_exactly_once(tmp_path):
    import json
    import scripts.continuous_flow_supervisor as flow
    original=flow.reconcile_merge_feedback
    flow.reconcile_merge_feedback=lambda root,merged: []
    merge_watcher_advanced=flow.merge_watcher_advanced
    payload={"updated_at_utc":"2026-09-19T22:00:00+00:00",
             "results":[{"repository":"r","pr":9,"head":"abc","merged":True}]}
    (tmp_path/"green-pr-watcher.json").write_text(json.dumps(payload),encoding="utf-8")
    assert merge_watcher_advanced(tmp_path) is True
    assert merge_watcher_advanced(tmp_path) is False
    flow.reconcile_merge_feedback=original

def test_new_merge_event_advances_after_previous_consumed(tmp_path):
    import json
    import scripts.continuous_flow_supervisor as flow
    original=flow.reconcile_merge_feedback
    flow.reconcile_merge_feedback=lambda root,merged: []
    merge_watcher_advanced=flow.merge_watcher_advanced
    path=tmp_path/"green-pr-watcher.json"
    path.write_text(json.dumps({"updated_at_utc":"t1","results":[{"repository":"r","pr":1,"head":"a","merged":True}]}),encoding="utf-8")
    assert merge_watcher_advanced(tmp_path) is True
    path.write_text(json.dumps({"updated_at_utc":"t2","results":[{"repository":"r","pr":2,"head":"b","merged":True}]}),encoding="utf-8")
    assert merge_watcher_advanced(tmp_path) is True
    flow.reconcile_merge_feedback=original

def test_refresh_readiness_fails_closed_for_incomplete_workspace(tmp_path):
    import json
    from scripts.continuous_flow_supervisor import refresh_readiness
    engineering=tmp_path/"engineering"; engineering.mkdir()
    (engineering/"hands-off-evolution.json").write_text(
        json.dumps({"products":{"voicestudio":["jweter/VoiceStudio"]}}),
        encoding="utf-8",
    )
    ok,reason=refresh_readiness(tmp_path,tmp_path/"workspace",tmp_path/"state")
    assert ok is False
    assert reason=="READINESS_WORKSPACE_INCOMPLETE"


def test_refresh_readiness_uses_contract_declared_voicestudio_workspace(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    import scripts.continuous_flow_supervisor as supervisor

    engineering=tmp_path/"engineering"; engineering.mkdir()
    (engineering/"hands-off-evolution.json").write_text(
        json.dumps({"products":{"voicestudio":["jweter/VoiceStudio"]}}),
        encoding="utf-8",
    )
    workspace=tmp_path/"workspace"; voice=workspace/"VoiceStudio"; voice.mkdir(parents=True)
    state=tmp_path/"state"; state.mkdir()
    captured={}

    class Result:
        returncode=0
        stdout=""
        stderr=""

    def run(cmd,**kwargs):
        if cmd[:3]==["git","worktree","add"] or cmd[:3]==["git","worktree","remove"]:
            return Result()
        captured["cmd"]=cmd
        activation=Path(cmd[cmd.index("--activation-state")+1])
        activation.write_text(
            json.dumps({
                "products":{
                    "voicestudio":{
                        "enabled":True,
                        "repositories":{"jweter/VoiceStudio":"voice-head"},
                    }
                }
            }),
            encoding="utf-8",
        )
        return Result()

    monkeypatch.setattr(supervisor.subprocess,"run",run)
    assert supervisor.refresh_readiness(tmp_path,workspace,state)==(
        True,"READINESS_REFRESHED"
    )
    audited=[Path(value) for value in captured["cmd"] if "readiness-worktrees" in str(value)]
    assert len(audited)==1 and audited[0].name=="VoiceStudio"
    assert str(voice) not in captured["cmd"]
    assert json.loads((state/"hands-off-expected-heads.json").read_text())=={
        "jweter/VoiceStudio":"voice-head"
    }

def test_evolution_authorized_returns_only_gate_current_products(tmp_path, monkeypatch):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    state=tmp_path/"state"; state.mkdir()
    (state/"hands-off-activation.json").write_text("{}",encoding="utf-8")
    (state/"hands-off-live-heads.json").write_text("{}",encoding="utf-8")
    monkeypatch.setattr(supervisor,"observe_live_heads",lambda *a:(True,"LIVE_HEADS_OBSERVED"))
    class Result:
        returncode=0
        stdout=json.dumps({"allowed":True,"reason":"ACTIVATION_CURRENT","products":{
            "knowledge-engine":{"allowed":False,"reason":"PRODUCT_HEADS_STALE"},
            "rocksmith":{"allowed":True,"reason":"PRODUCT_CURRENT"}}})
        stderr=""
    monkeypatch.setattr(supervisor.subprocess,"run",lambda *a,**k:Result())
    assert supervisor.evolution_authorized(tmp_path,tmp_path,state)==(True,"ACTIVATION_CURRENT",["rocksmith"],["knowledge-engine"])

def test_product_recertification_preserves_unrelated_authority(tmp_path):
    import json
    from scripts.continuous_flow_supervisor import merge_product_recertification
    state=tmp_path
    (state/"hands-off-activation.json").write_text(json.dumps({"schema_version":2,"products":{
        "rocksmith":{"enabled":True,"status":"PASS","repositories":{"jweter/rock":"old"}},
        "everward":{"enabled":True,"status":"PASS","repositories":{"jweter/ever":"stable"}}}}),encoding="utf-8")
    partial={"products":{"rocksmith":{"enabled":True,"status":"PASS","repositories":{"jweter/rock":"new"}}}}
    assert merge_product_recertification(state,"rocksmith",partial)==(True,"PRODUCT_RECERTIFIED")
    merged=json.loads((state/"hands-off-activation.json").read_text(encoding="utf-8"))
    assert merged["products"]["rocksmith"]["repositories"]["jweter/rock"]=="new"
    assert merged["products"]["everward"]["repositories"]["jweter/ever"]=="stable"
    assert json.loads((state/"hands-off-expected-heads.json").read_text())=={"jweter/ever":"stable","jweter/rock":"new"}

def test_failed_product_recertification_quarantines_only_product(tmp_path):
    import json
    from scripts.continuous_flow_supervisor import merge_product_recertification
    (tmp_path/"hands-off-activation.json").write_text(json.dumps({"schema_version":2,"products":{
        "rocksmith":{"enabled":True,"status":"PASS","repositories":{"jweter/rock":"old"}},
        "everward":{"enabled":True,"status":"PASS","repositories":{"jweter/ever":"stable"}}}}),encoding="utf-8")
    partial={"products":{"rocksmith":{"enabled":False,"status":"FAIL","repositories":{"jweter/rock":"new"}}}}
    assert merge_product_recertification(tmp_path,"rocksmith",partial)==(False,"PRODUCT_QUARANTINED")
    merged=json.loads((tmp_path/"hands-off-activation.json").read_text())
    assert merged["enabled"] is True
    assert merged["products"]["everward"]["enabled"] is True
    assert "jweter/rock" not in json.loads((tmp_path/"hands-off-expected-heads.json").read_text())

def test_recertify_product_runs_targeted_audit_and_merges(tmp_path, monkeypatch):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    state=tmp_path/"state"; state.mkdir()
    workspace=tmp_path/"workspace"; workspace.mkdir()
    for name in ["knowledge-engine-core","knowledge-engine-web","knowledge-engine-ai","rocksmith-cdlc-generator","Project-Everward"]:
        (workspace/name).mkdir()
    (tmp_path/"engineering").mkdir()
    (tmp_path/"engineering"/"hands-off-evolution.json").write_text(json.dumps({"products":{
        "rocksmith":["jweter/rocksmith-cdlc-generator"]}}))
    (state/"hands-off-activation.json").write_text(json.dumps({"schema_version":2,"products":{
        "rocksmith":{"enabled":True,"status":"PASS","repositories":{"jweter/rocksmith-cdlc-generator":"old"}},
        "everward":{"enabled":True,"status":"PASS","repositories":{"jweter/Project-Everward":"stable"}}}}),encoding="utf-8")
    class Result:
        returncode=0; stdout=""; stderr=""
    def run(cmd,**kwargs):
        if cmd[:3]==["git","worktree","add"] or cmd[:3]==["git","worktree","remove"]:
            return Result()
        assert cmd[cmd.index("--product")+1]=="rocksmith"
        assert any("readiness-worktrees" in str(value) and str(value).endswith("rocksmith-cdlc-generator") for value in cmd)
        partial=state/"hands-off-recertify-rocksmith.json"
        partial.write_text(json.dumps({"products":{"rocksmith":{
            "enabled":True,"status":"PASS","repositories":{"jweter/rocksmith-cdlc-generator":"new"}}}}),encoding="utf-8")
        return Result()
    monkeypatch.setattr(supervisor.subprocess,"run",run)
    assert supervisor.recertify_product(tmp_path,workspace,state,"rocksmith")== (True,"PRODUCT_RECERTIFIED")
    merged=json.loads((state/"hands-off-activation.json").read_text())
    assert merged["products"]["rocksmith"]["repositories"]["jweter/rocksmith-cdlc-generator"]=="new"
    assert merged["products"]["everward"]["repositories"]["jweter/Project-Everward"]=="stable"

def test_observe_live_heads_reads_git_not_activation(tmp_path, monkeypatch):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    repo_root=tmp_path/"orchestrator"; repo_root.mkdir()
    engineering=repo_root/"engineering"; engineering.mkdir()
    engineering.joinpath("hands-off-evolution.json").write_text(json.dumps({
        "products":{"rocksmith":["jweter/rocksmith-cdlc-generator"]}}),encoding="utf-8")
    workspace=tmp_path/"workspace"; ws=workspace/"rocksmith-cdlc-generator"; ws.mkdir(parents=True)
    state=tmp_path/"state"; state.mkdir()
    class Result:
        def __init__(self,stdout):
            self.returncode=0; self.stdout=stdout; self.stderr=""
    def run(cmd,**kwargs):
        if cmd[:3]==["git","remote","get-url"]:
            return Result("https://github.com/jweter/rocksmith-cdlc-generator.git\n")
        if cmd[:3]==["git","ls-remote","origin"]:
            return Result("live-sha\trefs/heads/main\n")
        if cmd[:3]==["git","rev-parse","refs/remotes/origin/main"]:
            return Result("live-sha\n")
        raise AssertionError(cmd)
    monkeypatch.setattr(supervisor.subprocess,"run",run)
    assert supervisor.observe_live_heads(repo_root,workspace,state)==(True,"LIVE_HEADS_OBSERVED")
    assert json.loads((state/"hands-off-live-heads.json").read_text())=={
        "jweter/rocksmith-cdlc-generator":"live-sha"}

def test_observe_live_heads_refreshes_stale_origin_main_without_moving_workspace_head(tmp_path, monkeypatch):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    repo_root=tmp_path/"orchestrator"; repo_root.mkdir()
    engineering=repo_root/"engineering"; engineering.mkdir()
    engineering.joinpath("hands-off-evolution.json").write_text(json.dumps({
        "products":{"voicestudio":["jweter/VoiceStudio"]}}),encoding="utf-8")
    workspace=tmp_path/"workspace"; ws=workspace/"VoiceStudio"; ws.mkdir(parents=True)
    state=tmp_path/"state"; state.mkdir(); calls=[]
    class Result:
        def __init__(self,stdout="",returncode=0): self.returncode=returncode; self.stdout=stdout; self.stderr=""
    def run(cmd,**kwargs):
        calls.append(cmd)
        if cmd[:3]==["git","remote","get-url"]: return Result("https://github.com/jweter/VoiceStudio.git\n")
        if cmd[:3]==["git","ls-remote","origin"]: return Result("new-main\trefs/heads/main\n")
        if cmd[:3]==["git","rev-parse","refs/remotes/origin/main"]: return Result("old-main\n")
        if cmd[:3]==["git","fetch","origin"]: return Result()
        raise AssertionError(cmd)
    monkeypatch.setattr(supervisor.subprocess,"run",run)
    assert supervisor.observe_live_heads(repo_root,workspace,state)==(True,"LIVE_HEADS_OBSERVED")
    assert json.loads((state/"hands-off-live-heads.json").read_text())=={"jweter/VoiceStudio":"new-main"}
    assert ["git","fetch","origin","main"] in calls
    assert not any(cmd[:3]==["git","rev-parse","HEAD"] for cmd in calls)

def test_observe_live_heads_probes_independent_workspaces_concurrently(tmp_path):
    import json
    from threading import Barrier
    from project_orchestrator.activation_capability import observe_live_heads

    repo_root=tmp_path/"actor"
    (repo_root/"engineering").mkdir(parents=True)
    (repo_root/"engineering"/"hands-off-evolution.json").write_text(
        json.dumps({"products":{"x":["jweter/VoiceStudio","jweter/Project-Everward"]}}))
    workspace_root=tmp_path/"workspaces"
    (workspace_root/"VoiceStudio").mkdir(parents=True)
    (workspace_root/"Project-Everward").mkdir(parents=True)
    state_root=tmp_path/"state"
    state_root.mkdir()
    rendezvous=Barrier(2)

    class Result:
        returncode=0
        def __init__(self,stdout): self.stdout=stdout

    def run(argv,**kwargs):
        assert kwargs["timeout"] <= 30
        name=kwargs["cwd"].name
        if argv[:3]==["git","remote","get-url"]:
            return Result(f"https://github.com/jweter/{name}.git\n")
        if argv[:3]==["git","ls-remote","origin"]:
            rendezvous.wait(timeout=5)
            return Result(f"{name}-sha\trefs/heads/main\n")
        if argv[:3]==["git","rev-parse","refs/remotes/origin/main"]:
            return Result(f"{name}-sha\n")
        raise AssertionError(argv)

    assert observe_live_heads(repo_root,workspace_root,state_root,run_fn=run)==(
        True,"LIVE_HEADS_OBSERVED")
    assert json.loads((state_root/"hands-off-live-heads.json").read_text())=={
        "jweter/VoiceStudio":"VoiceStudio-sha",
        "jweter/Project-Everward":"Project-Everward-sha",
    }


def test_observe_live_heads_ignores_origin_mismatch(tmp_path, monkeypatch):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    repo_root=tmp_path/"orchestrator"; repo_root.mkdir()
    engineering=repo_root/"engineering"; engineering.mkdir()
    engineering.joinpath("hands-off-evolution.json").write_text(json.dumps({
        "products":{"rocksmith":["jweter/rocksmith-cdlc-generator"]}}),encoding="utf-8")
    workspace=tmp_path/"workspace"; (workspace/"rocksmith-cdlc-generator").mkdir(parents=True)
    state=tmp_path/"state"; state.mkdir()
    class Result:
        returncode=0; stderr=""
        def __init__(self,stdout): self.stdout=stdout
    def run(cmd,**kwargs):
        return Result("https://github.com/attacker/wrong.git\n") if "remote" in cmd else Result("sha\n")
    monkeypatch.setattr(supervisor.subprocess,"run",run)
    assert supervisor.observe_live_heads(repo_root,workspace,state)==(False,"LIVE_HEADS_UNAVAILABLE")
    assert json.loads((state/"hands-off-live-heads.json").read_text())=={}

def test_legacy_stale_claim_without_worker_identity_fails_closed(tmp_path):
    import json
    from datetime import UTC, datetime, timedelta
    import scripts.continuous_flow_supervisor as supervisor
    state=tmp_path
    now=datetime(2026,9,19,23,0,tzinfo=UTC)
    old=(now-timedelta(seconds=supervisor.CLAIM_LEASE_SECONDS+1)).isoformat()
    (state/"hands-off-grant-lifecycle.json").write_text(json.dumps({
        "schema_version":1,"grants":{"g1":{"state":"CLAIMED","claimed_at":old}}}),encoding="utf-8")
    assert supervisor.recover_stale_grant_claims(state,now)==[]
    record=json.loads((state/"hands-off-grant-lifecycle.json").read_text())["grants"]["g1"]
    assert record["state"]=="CLAIMED"
    assert record["worker_liveness"]=="UNKNOWN"
    assert record["result_status"]=="CLAIM_LEASE_EXPIRED_WORKER_UNKNOWN"
    assert record["last_liveness_check_at"]==now.isoformat()

def test_recover_stale_grant_claims_preserves_live_claim(tmp_path):
    import json
    from datetime import UTC, datetime, timedelta
    import scripts.continuous_flow_supervisor as supervisor
    now=datetime(2026,9,19,23,0,tzinfo=UTC)
    recent=(now-timedelta(seconds=supervisor.CLAIM_LEASE_SECONDS-1)).isoformat()
    path=tmp_path/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"schema_version":1,"grants":{
        "g1":{"state":"CLAIMED","claimed_at":recent}}}),encoding="utf-8")
    assert supervisor.recover_stale_grant_claims(tmp_path,now)==[]
    assert json.loads(path.read_text())["grants"]["g1"]["state"]=="CLAIMED"

def test_recover_stale_grant_claims_never_reopens_terminal_claim(tmp_path):
    import json
    from datetime import UTC, datetime, timedelta
    import scripts.continuous_flow_supervisor as supervisor
    now=datetime(2026,9,19,23,0,tzinfo=UTC)
    old=(now-timedelta(hours=2)).isoformat()
    path=tmp_path/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"schema_version":1,"grants":{
        "done":{"state":"COMPLETED","claimed_at":old},
        "failed":{"state":"FAILED","claimed_at":old}}}),encoding="utf-8")
    assert supervisor.recover_stale_grant_claims(tmp_path,now)==[]

def test_recover_stale_grant_claims_uses_last_heartbeat(tmp_path):
    import json
    from datetime import UTC, datetime, timedelta
    import scripts.continuous_flow_supervisor as supervisor
    now=datetime(2026,9,19,23,0,tzinfo=UTC)
    old=(now-timedelta(hours=2)).isoformat()
    fresh=(now-timedelta(seconds=5)).isoformat()
    path=tmp_path/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"schema_version":1,"grants":{"g1":{
        "state":"CLAIMED","claimed_at":old,"last_heartbeat_at":fresh}}}),encoding="utf-8")
    assert supervisor.recover_stale_grant_claims(tmp_path,now)==[]
    assert json.loads(path.read_text())["grants"]["g1"]["state"]=="CLAIMED"

def test_reconcile_abandoned_claim_quarantines_dirty_workspace(tmp_path, monkeypatch):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    wsroot=tmp_path/"workspace"; ws=(wsroot/"rock"); ws.mkdir(parents=True)
    state=tmp_path/"state"; state.mkdir()
    path=state/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"schema_version":1,"grants":{"g1":{
        "state":"ABANDONED","repository":"jweter/rock"}}}),encoding="utf-8")
    class Result:
        returncode=0
        def __init__(self,stdout): self.stdout=stdout; self.stderr=""
    def run(cmd,**kwargs):
        return Result("modified.txt\n") if cmd[1:3]==["status","--porcelain"] else Result("actor/autonomous-rock-x\n")
    monkeypatch.setattr(supervisor.subprocess,"run",run)
    result=supervisor.reconcile_abandoned_claims(wsroot,state,["g1"])
    assert result=={"g1":"DIRTY_WORKSPACE_QUARANTINED"}
    assert json.loads(path.read_text())["grants"]["g1"]["recovery_result"]=="DIRTY_WORKSPACE_QUARANTINED"

def test_reconcile_abandoned_claim_recovers_clean_workspace(tmp_path, monkeypatch):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    wsroot=tmp_path/"workspace"; (wsroot/"rock").mkdir(parents=True)
    state=tmp_path/"state"; state.mkdir()
    path=state/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"schema_version":1,"grants":{"g1":{
        "state":"ABANDONED","repository":"jweter/rock"}}}),encoding="utf-8")
    class Result:
        returncode=0; stderr=""
        def __init__(self,stdout): self.stdout=stdout
    def run(cmd,**kwargs):
        return Result("") if cmd[1:3]==["status","--porcelain"] else Result("main\n")
    monkeypatch.setattr(supervisor.subprocess,"run",run)
    assert supervisor.reconcile_abandoned_claims(wsroot,state,["g1"])=={"g1":"CLEAN_WORKSPACE_RECOVERED"}
    record=json.loads(path.read_text())["grants"]["g1"]
    assert record["recovery_state"]=="RECOVERED"
    assert record["recovered_at"]

def test_reconcile_abandoned_claim_quarantines_orphan_actor_branch(tmp_path, monkeypatch):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    wsroot=tmp_path/"workspace"; (wsroot/"rock").mkdir(parents=True)
    state=tmp_path/"state"; state.mkdir()
    path=state/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"schema_version":1,"grants":{"g1":{
        "state":"ABANDONED","repository":"jweter/rock"}}}),encoding="utf-8")
    class Result:
        returncode=0; stderr=""
        def __init__(self,stdout): self.stdout=stdout
    def run(cmd,**kwargs):
        return Result("") if cmd[1:3]==["status","--porcelain"] else Result("actor/autonomous-rock-dead\n")
    monkeypatch.setattr(supervisor.subprocess,"run",run)
    # A clean actor branch with no commits beyond origin/main is proven harmless.
    assert supervisor.reconcile_abandoned_claims(wsroot,state,["g1"])=={"g1":"ORPHAN_NO_DIFF"}

def test_classify_orphan_branch_with_matching_open_pr_is_recoverable(tmp_path, monkeypatch):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    class Result:
        returncode=0; stderr=""
        def __init__(self,stdout): self.stdout=stdout
    def run(cmd,**kwargs):
        if cmd[:2]==["git","merge-base"]: return Result("base\n")
        if cmd[:2]==["git","rev-parse"]: return Result("head\n")
        if cmd[:2]==["git","ls-remote"]: return Result("head\trefs/heads/actor/autonomous-x\n")
        if cmd[:3]==["gh","pr","list"]: return Result(json.dumps([{"number":9,"url":"u","headRefOid":"head"}]))
        raise AssertionError(cmd)
    monkeypatch.setattr(supervisor.subprocess,"run",run)
    status,evidence=supervisor.classify_orphan_branch("jweter/rock",tmp_path,"actor/autonomous-x")
    assert status=="ORPHAN_PR_RECOVERABLE"
    assert evidence["pr_number"]==9

def test_classify_orphan_branch_detects_unpushed_commit(tmp_path, monkeypatch):
    import scripts.continuous_flow_supervisor as supervisor
    class Result:
        returncode=0; stderr=""
        def __init__(self,stdout): self.stdout=stdout
    def run(cmd,**kwargs):
        if cmd[:2]==["git","merge-base"]: return Result("base\n")
        if cmd[:2]==["git","rev-parse"]: return Result("head\n")
        if cmd[:2]==["git","ls-remote"]: return Result("")
        if cmd[:3]==["gh","pr","list"]: return Result("[]")
        raise AssertionError(cmd)
    monkeypatch.setattr(supervisor.subprocess,"run",run)
    status,evidence=supervisor.classify_orphan_branch("jweter/rock",tmp_path,"actor/autonomous-x")
    assert status=="ORPHAN_LOCAL_COMMIT_UNPUSHED"
    assert evidence["head_sha"]=="head"

def test_classify_orphan_branch_detects_pushed_branch_without_pr(tmp_path, monkeypatch):
    import scripts.continuous_flow_supervisor as supervisor
    class Result:
        returncode=0; stderr=""
        def __init__(self,stdout): self.stdout=stdout
    def run(cmd,**kwargs):
        if cmd[:2]==["git","merge-base"]: return Result("base\n")
        if cmd[:2]==["git","rev-parse"]: return Result("head\n")
        if cmd[:2]==["git","ls-remote"]: return Result("head\trefs/heads/actor/autonomous-x\n")
        if cmd[:3]==["gh","pr","list"]: return Result("[]")
        raise AssertionError(cmd)
    monkeypatch.setattr(supervisor.subprocess,"run",run)
    assert supervisor.classify_orphan_branch("jweter/rock",tmp_path,"actor/autonomous-x")[0]=="ORPHAN_PUSHED_NO_PR"

def test_resume_unpushed_orphan_verifies_preflight_push_remote_and_opens_pr(tmp_path, monkeypatch):
    import scripts.continuous_flow_supervisor as supervisor
    (tmp_path/"engineering").mkdir(); (tmp_path/"engineering"/"preflight.py").write_text("pass")
    calls=[]
    class Result:
        returncode=0; stderr=""
        def __init__(self,stdout=""): self.stdout=stdout
    def run(cmd,**kwargs):
        calls.append(cmd)
        if cmd[:2]==["git","rev-parse"]: return Result("head\n")
        if cmd[:2]==["git","ls-remote"]: return Result("head\trefs/heads/actor/autonomous-x\n")
        return Result("https://github/pr/1\n" if cmd[:3]==["gh","pr","create"] else "")
    monkeypatch.setattr(supervisor.subprocess,"run",run)
    status=supervisor.resume_orphan_branch("jweter/rock",tmp_path,"actor/autonomous-x",
        "ORPHAN_LOCAL_COMMIT_UNPUSHED",{"head_sha":"head","remote_head_sha":""})
    assert status=="RECOVERY_PR_OPENED"
    assert any(cmd[:2]==["git","push"] for cmd in calls)
    assert any(cmd[:3]==["gh","pr","create"] for cmd in calls)

def test_resume_orphan_fails_closed_when_local_head_changed(tmp_path, monkeypatch):
    import scripts.continuous_flow_supervisor as supervisor
    class Result:
        returncode=0; stderr=""; stdout="different\n"
    monkeypatch.setattr(supervisor.subprocess,"run",lambda *a,**k:Result())
    assert supervisor.resume_orphan_branch("jweter/rock",tmp_path,"actor/autonomous-x",
        "ORPHAN_LOCAL_COMMIT_UNPUSHED",{"head_sha":"expected"})=="RECOVERY_HEAD_CHANGED"

def test_resume_pushed_orphan_requires_exact_remote_head(tmp_path, monkeypatch):
    import scripts.continuous_flow_supervisor as supervisor
    class Result:
        returncode=0; stderr=""
        def __init__(self,stdout): self.stdout=stdout
    calls=[]
    def run(cmd,**kwargs):
        calls.append(cmd)
        if cmd[:2]==["git","rev-parse"]: return Result("head\n")
        if cmd[:2]==["git","ls-remote"]: return Result("other\trefs/heads/actor/autonomous-x\n")
        raise AssertionError("PR creation must not occur")
    monkeypatch.setattr(supervisor.subprocess,"run",run)
    assert supervisor.resume_orphan_branch("jweter/rock",tmp_path,"actor/autonomous-x",
        "ORPHAN_PUSHED_NO_PR",{"head_sha":"head","remote_head_sha":"head"})=="RECOVERY_REMOTE_HEAD_MISMATCH"

def test_recertify_product_passes_only_target_product_repositories(tmp_path, monkeypatch):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    repo_root=tmp_path/"orchestrator"; workspace=tmp_path/"workspace"; state=tmp_path/"state"
    (repo_root/"engineering").mkdir(parents=True); state.mkdir()
    contract={"products":{"rocksmith":["jweter/rocksmith-cdlc-generator"],
                          "knowledge-engine":["jweter/knowledge-engine-core","jweter/knowledge-engine-web"]}}
    (repo_root/"engineering"/"hands-off-evolution.json").write_text(json.dumps(contract))
    (workspace/"rocksmith-cdlc-generator").mkdir(parents=True)
    calls=[]
    class Result:
        def __init__(self,returncode=1): self.returncode=returncode; self.stdout=""; self.stderr=""
    def run(cmd,**kwargs):
        calls.append(cmd)
        if cmd[:3]==["git","worktree","add"] or cmd[:3]==["git","worktree","remove"]:
            return Result(0)
        # Simulate auditor writing a readable partial authority.
        partial=state/"hands-off-recertify-rocksmith.json"
        partial.write_text(json.dumps({"products":{"rocksmith":{"enabled":False,"repositories":{}}}}))
        return Result()
    monkeypatch.setattr(supervisor.subprocess,"run",run)
    monkeypatch.setattr(supervisor,"merge_product_recertification",lambda *a:(False,"PRODUCT_QUARANTINED"))
    supervisor.recertify_product(repo_root,workspace,state,"rocksmith")
    cmd=next(call for call in calls if "--product" in call)
    assert any("readiness-worktrees" in str(value) and str(value).endswith("rocksmith-cdlc-generator") for value in cmd)
    assert not any(str(value).endswith("knowledge-engine-core") or str(value).endswith("knowledge-engine-web") for value in cmd)

def test_recertify_unknown_product_fails_before_auditor(tmp_path, monkeypatch):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    repo_root=tmp_path/"orchestrator"; (repo_root/"engineering").mkdir(parents=True)
    (repo_root/"engineering"/"hands-off-evolution.json").write_text(json.dumps({"products":{"rocksmith":["jweter/rock"]}}))
    monkeypatch.setattr(supervisor.subprocess,"run",lambda *a,**k:(_ for _ in ()).throw(AssertionError("must not audit")))
    assert supervisor.recertify_product(repo_root,tmp_path/"workspace",tmp_path/"state","unknown")==(
        False,"RECERTIFICATION_CONTRACT_UNREADABLE")

def test_run_builder_refuses_dispatch_without_authorized_products(tmp_path, monkeypatch):
    import scripts.continuous_flow_supervisor as supervisor
    called=False
    def run(*a,**k):
        nonlocal called
        called=True
        raise AssertionError("builder must not run without grant scope")
    monkeypatch.setattr(supervisor.subprocess,"run",run)
    state=tmp_path/"state"; state.mkdir()
    rc,invocation_id=supervisor.run_builder(tmp_path,tmp_path/"workspace",state,None)
    assert rc==2
    assert invocation_id is None
    assert called is False
    assert "EXECUTION_GRANT_REQUIRED" in (state/"builder-last-output.txt").read_text()

def test_pending_recovery_queue_rediscovers_old_abandoned_claims(tmp_path):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    state=tmp_path/"state"; state.mkdir()
    (state/"hands-off-grant-lifecycle.json").write_text(json.dumps({"grants":{
        "old":{"state":"ABANDONED","recovery_result":"RECOVERY_PUSH_FAILED"},
        "done":{"state":"ABANDONED","recovery_state":"RECOVERED"},
        "live":{"state":"CLAIMED"}}}))
    assert supervisor.pending_recovery_grants(state,["new"])==["new","old"]

def test_recovery_queue_uses_bounded_backoff_and_eventual_quarantine(tmp_path):
    import json
    from datetime import datetime, UTC
    import scripts.continuous_flow_supervisor as supervisor
    state=tmp_path/"state"; state.mkdir()
    workspace=tmp_path/"workspace"; workspace.mkdir()
    lifecycle=state/"hands-off-grant-lifecycle.json"
    lifecycle.write_text(json.dumps({"grants":{"g":{"state":"ABANDONED","authorized_repositories":[]}}}))
    now=datetime(2026,1,1,tzinfo=UTC)
    first=supervisor.reconcile_abandoned_claims(workspace,state,["g"],now)
    assert first["g"]=="RECOVERY_IDENTITY_AMBIGUOUS"
    saved=json.loads(lifecycle.read_text())["grants"]["g"]
    assert saved["recovery_attempts"]==1
    assert saved["recovery_next_attempt_at"]
    assert supervisor.reconcile_abandoned_claims(workspace,state,["g"],now)["g"]=="RECOVERY_BACKOFF"
    saved["recovery_attempts"]=5
    saved.pop("recovery_next_attempt_at",None)
    lifecycle.write_text(json.dumps({"grants":{"g":saved}}))
    final=supervisor.reconcile_abandoned_claims(workspace,state,["g"],now)
    assert final["g"]=="RECOVERY_RETRY_EXHAUSTED"
    assert json.loads(lifecycle.read_text())["grants"]["g"]["recovery_state"]=="QUARANTINED"

def test_fresh_claim_with_dead_worker_is_released_immediately(tmp_path):
    import json
    from datetime import UTC, datetime, timedelta
    import scripts.continuous_flow_supervisor as supervisor
    state=tmp_path/"state"; state.mkdir()
    now=datetime(2026,1,1,tzinfo=UTC); fresh=(now-timedelta(seconds=5)).isoformat()
    path=state/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{"g":{"state":"CLAIMED","claimed_at":fresh,"last_heartbeat_at":fresh,"worker":{"host":"h","pid":9}}}}))
    assert supervisor.recover_stale_grant_claims(state,now,lambda worker:"DEAD")==["g"]
    saved=json.loads(path.read_text())["grants"]["g"]
    assert saved["state"]=="ABANDONED"
    assert saved["result_status"]=="CLAIM_WORKER_DEAD_FAST_RECOVERY"


def test_fresh_claim_with_unknown_worker_preserves_lease(tmp_path):
    import json
    from datetime import UTC, datetime, timedelta
    import scripts.continuous_flow_supervisor as supervisor
    state=tmp_path/"state"; state.mkdir()
    now=datetime(2026,1,1,tzinfo=UTC); fresh=(now-timedelta(seconds=5)).isoformat()
    path=state/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{"g":{"state":"CLAIMED","claimed_at":fresh,"last_heartbeat_at":fresh,"worker":None}}}))
    assert supervisor.recover_stale_grant_claims(state,now,lambda worker:"UNKNOWN")==[]
    saved=json.loads(path.read_text())["grants"]["g"]
    assert saved["state"]=="CLAIMED"
    assert "worker_liveness" not in saved


def test_stale_claim_with_live_worker_is_not_abandoned(tmp_path):
    import json
    from datetime import UTC, datetime, timedelta
    import scripts.continuous_flow_supervisor as supervisor
    state=tmp_path/"state"; state.mkdir()
    now=datetime(2026,1,1,tzinfo=UTC); old=(now-timedelta(seconds=supervisor.CLAIM_LEASE_SECONDS+1)).isoformat()
    path=state/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{"g":{"state":"CLAIMED","claimed_at":old,"last_heartbeat_at":old,"worker":{"host":"h","pid":1}}}}))
    assert supervisor.recover_stale_grant_claims(state,now,lambda worker:"ALIVE")==[]
    saved=json.loads(path.read_text())["grants"]["g"]
    assert saved["state"]=="CLAIMED" and saved["worker_liveness"]=="ALIVE"

def test_stale_claim_with_unknown_liveness_fails_closed(tmp_path):
    import json
    from datetime import UTC, datetime, timedelta
    import scripts.continuous_flow_supervisor as supervisor
    state=tmp_path/"state"; state.mkdir()
    now=datetime(2026,1,1,tzinfo=UTC); old=(now-timedelta(seconds=supervisor.CLAIM_LEASE_SECONDS+1)).isoformat()
    path=state/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{"g":{"state":"CLAIMED","claimed_at":old,"last_heartbeat_at":old,"worker":None}}}))
    assert supervisor.recover_stale_grant_claims(state,now,lambda worker:"UNKNOWN")==[]
    assert json.loads(path.read_text())["grants"]["g"]["state"]=="CLAIMED"

def test_stale_claim_is_abandoned_only_when_worker_proven_dead(tmp_path):
    import json
    from datetime import UTC, datetime, timedelta
    import scripts.continuous_flow_supervisor as supervisor
    state=tmp_path/"state"; state.mkdir()
    now=datetime(2026,1,1,tzinfo=UTC); old=(now-timedelta(seconds=supervisor.CLAIM_LEASE_SECONDS+1)).isoformat()
    path=state/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{"g":{"state":"CLAIMED","claimed_at":old,"last_heartbeat_at":old,"worker":{"host":"h","pid":9}}}}))
    assert supervisor.recover_stale_grant_claims(state,now,lambda worker:"DEAD")==["g"]
    saved=json.loads(path.read_text())["grants"]["g"]
    assert saved["state"]=="ABANDONED"
    assert saved["result_status"]=="CLAIM_LEASE_EXPIRED_WORKER_DEAD"

def test_worker_liveness_rejects_pid_reuse(monkeypatch):
    import socket
    import scripts.continuous_flow_supervisor as supervisor
    import scripts.autonomous_slice_builder as builder
    import project_orchestrator.recovery_manager as recovery
    monkeypatch.setattr(recovery.sys, "platform", "linux")
    monkeypatch.setattr(supervisor.os,"kill",lambda pid,sig: None)
    monkeypatch.setattr(builder,"process_start_fingerprint",lambda pid:"linux:boot:new")
    worker={"host":socket.gethostname(),"pid":77,"process_start_fingerprint":"linux:boot:old"}
    assert supervisor.worker_liveness(worker)=="DEAD"

def test_worker_liveness_treats_unreadable_fingerprinted_pid_as_unknown(monkeypatch):
    import socket
    import scripts.continuous_flow_supervisor as supervisor
    import scripts.autonomous_slice_builder as builder
    import project_orchestrator.recovery_manager as recovery
    monkeypatch.setattr(recovery.sys, "platform", "linux")
    monkeypatch.setattr(builder,"process_start_fingerprint",lambda pid:None)
    monkeypatch.setattr(supervisor.os,"kill",lambda pid,sig: (_ for _ in ()).throw(OSError("Windows invalid parameter")))
    worker={"host":socket.gethostname(),"pid":77,"process_start_fingerprint":"windows:old"}
    assert supervisor.worker_liveness(worker)=="UNKNOWN"

def test_worker_liveness_uses_nondestructive_windows_probe(monkeypatch):
    import socket
    import project_orchestrator.recovery_manager as recovery
    import scripts.continuous_flow_supervisor as supervisor
    monkeypatch.setattr(recovery.sys, "platform", "win32")
    monkeypatch.setattr(recovery, "_windows_pid_liveness", lambda pid: "DEAD")
    def forbidden_kill(pid, sig):
        raise AssertionError("Windows worker liveness must never signal a PID")
    monkeypatch.setattr(supervisor.os, "kill", forbidden_kill)
    worker={"host":socket.gethostname(),"pid":77,"process_start_fingerprint":"windows:old"}
    assert supervisor.worker_liveness(worker)=="DEAD"

def test_worker_liveness_accepts_matching_process_fingerprint(monkeypatch):
    import socket
    import scripts.continuous_flow_supervisor as supervisor
    import scripts.autonomous_slice_builder as builder
    import project_orchestrator.recovery_manager as recovery
    monkeypatch.setattr(recovery.sys, "platform", "linux")
    monkeypatch.setattr(supervisor.os,"kill",lambda pid,sig: None)
    monkeypatch.setattr(builder,"process_start_fingerprint",lambda pid:"linux:boot:same")
    worker={"host":socket.gethostname(),"pid":77,"process_start_fingerprint":"linux:boot:same"}
    assert supervisor.worker_liveness(worker)=="ALIVE"

def test_unknown_worker_liveness_is_bounded_by_quarantine(tmp_path):
    import json
    from datetime import UTC, datetime, timedelta
    import scripts.continuous_flow_supervisor as supervisor
    now=datetime(2026,1,1,tzinfo=UTC)
    old=(now-timedelta(seconds=supervisor.CLAIM_LEASE_SECONDS+1)).isoformat()
    path=tmp_path/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{"g":{"state":"CLAIMED","claimed_at":old,"last_heartbeat_at":old}}}))
    for i in range(supervisor.UNKNOWN_LIVENESS_MAX_CHECKS-1):
        assert supervisor.recover_stale_grant_claims(tmp_path,now+timedelta(seconds=i),lambda worker:"UNKNOWN")==[]
        assert json.loads(path.read_text())["grants"]["g"]["state"]=="CLAIMED"
    assert supervisor.recover_stale_grant_claims(tmp_path,now+timedelta(seconds=99),lambda worker:"UNKNOWN")==[]
    saved=json.loads(path.read_text())["grants"]["g"]
    assert saved["state"]=="QUARANTINED"
    assert saved["result_status"]=="CLAIM_LEASE_UNKNOWN_QUARANTINED"
    assert saved["unknown_liveness_checks"]==supervisor.UNKNOWN_LIVENESS_MAX_CHECKS

def test_live_worker_resets_unknown_liveness_budget(tmp_path):
    import json
    from datetime import UTC, datetime, timedelta
    import scripts.continuous_flow_supervisor as supervisor
    now=datetime(2026,1,1,tzinfo=UTC)
    old=(now-timedelta(seconds=supervisor.CLAIM_LEASE_SECONDS+1)).isoformat()
    path=tmp_path/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{"g":{"state":"CLAIMED","claimed_at":old,"last_heartbeat_at":old,"unknown_liveness_checks":3,"unknown_liveness_first_seen_at":old}}}))
    supervisor.recover_stale_grant_claims(tmp_path,now,lambda worker:"ALIVE")
    saved=json.loads(path.read_text())["grants"]["g"]
    assert saved["state"]=="CLAIMED"
    assert saved["unknown_liveness_checks"]==0
    assert "unknown_liveness_first_seen_at" not in saved

def test_quarantined_grants_exposes_safe_actionable_evidence(tmp_path):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    path=tmp_path/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{
        "q":{"state":"QUARANTINED","repository":"o/r","result_status":"CLAIM_LEASE_UNKNOWN_QUARANTINED",
             "unknown_liveness_checks":5,"worker":{"host":"h","pid":7,"process_start_fingerprint":"fp","lease_token":"SECRET"}},
        "done":{"state":"COMPLETED","worker":{"lease_token":"OTHER"}}
    }}))
    result=supervisor.quarantined_grants(tmp_path)
    assert set(result)=={"q"}
    assert result["q"]["repository"]=="o/r"
    assert result["q"]["worker_pid"]==7
    assert "lease_token" not in result["q"]
    assert "SECRET" not in json.dumps(result)

def test_quarantine_recheck_releases_only_proven_dead_worker(tmp_path):
    import json
    from datetime import UTC, datetime
    import scripts.continuous_flow_supervisor as supervisor
    now=datetime(2026,1,1,tzinfo=UTC); path=tmp_path/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{"g":{"state":"QUARANTINED","result_status":"CLAIM_LEASE_UNKNOWN_QUARANTINED","worker":{"host":"h","pid":7}}}}))
    assert supervisor.recheck_quarantined_claims(tmp_path,now,lambda worker:"DEAD")=={"g":"RELEASED_TO_RECOVERY"}
    saved=json.loads(path.read_text())["grants"]["g"]
    assert saved["state"]=="ABANDONED"
    assert saved["result_status"]=="CLAIM_LEASE_QUARANTINE_WORKER_DEAD"

def test_quarantine_recheck_releases_proven_dead_issuer(tmp_path):
    import json
    from datetime import UTC, datetime
    import scripts.continuous_flow_supervisor as supervisor
    now=datetime(2026,1,1,tzinfo=UTC); path=tmp_path/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{"g":{"state":"QUARANTINED","result_status":"ISSUE_LEASE_UNKNOWN_QUARANTINED","issuer":{"host":"h","pid":7}}}}))
    seen=[]
    assert supervisor.recheck_quarantined_claims(tmp_path,now,lambda owner:seen.append(owner) or "DEAD")=={"g":"RELEASED_TO_RECOVERY"}
    saved=json.loads(path.read_text())["grants"]["g"]
    assert seen==[{"host":"h","pid":7}]
    assert saved["state"]=="ABANDONED"
    assert saved["result_status"]=="ISSUE_LEASE_QUARANTINE_ISSUER_DEAD"

def test_quarantine_recheck_restores_proven_live_worker(tmp_path):
    import json
    from datetime import UTC, datetime
    import scripts.continuous_flow_supervisor as supervisor
    now=datetime(2026,1,1,tzinfo=UTC); path=tmp_path/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{"g":{"state":"QUARANTINED","result_status":"CLAIM_LEASE_UNKNOWN_QUARANTINED","unknown_liveness_checks":5,"finished_at":"old","worker":{"host":"h","pid":7}}}}))
    assert supervisor.recheck_quarantined_claims(tmp_path,now,lambda worker:"ALIVE")=={"g":"RESTORED_TO_CLAIMED"}
    saved=json.loads(path.read_text())["grants"]["g"]
    assert saved["state"]=="CLAIMED"
    assert saved["unknown_liveness_checks"]==0
    assert "finished_at" not in saved

def test_quarantine_recheck_preserves_unknown_ownership(tmp_path):
    import json
    from datetime import UTC, datetime
    import scripts.continuous_flow_supervisor as supervisor
    now=datetime(2026,1,1,tzinfo=UTC); path=tmp_path/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{"g":{"state":"QUARANTINED","result_status":"CLAIM_LEASE_UNKNOWN_QUARANTINED"}}}))
    assert supervisor.recheck_quarantined_claims(tmp_path,now,lambda worker:"UNKNOWN")=={"g":"STILL_UNKNOWN"}
    saved=json.loads(path.read_text())["grants"]["g"]
    assert saved["state"]=="QUARANTINED"
    assert saved["quarantine_rechecks"]==1



def test_transport_recovery_uses_recorded_worker_worktree(tmp_path,monkeypatch):
    import json
    from pathlib import Path
    from datetime import UTC, datetime
    import scripts.continuous_flow_supervisor as supervisor
    wsroot=tmp_path/"ws"; canonical=wsroot/"r"; canonical.mkdir(parents=True)
    worker=wsroot/".worker-worktrees"/"worker-1"/"r"; worker.mkdir(parents=True)
    state=tmp_path/"state"; state.mkdir()
    path=state/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{"g":{"state":"ABANDONED","recovery_state":"PENDING",
        "repository":"o/r","execution_stage":"COMMITTED","branch":"actor/x","head_sha":"head",
        "workspace_path":str(worker),"recovery_attempts":0}}}))
    class R:
        returncode=0; stderr=""
        def __init__(self,out=""): self.stdout=out
    seen=[]
    def fake(cmd,**kwargs):
        seen.append(Path(kwargs["cwd"]))
        if cmd[1:3]==["branch","--show-current"]: return R("actor/x\n")
        if cmd[1:3]==["rev-parse","HEAD"]: return R("head\n")
        if cmd[1:3]==["ls-remote","--heads"]: return R("head refs/heads/actor/x\n")
        return R("")
    monkeypatch.setattr(supervisor.subprocess,"run",fake)
    monkeypatch.setattr(supervisor,"resume_orphan_branch",lambda repo,ws,*a: "RECOVERY_PR_OPENED")
    import project_orchestrator.workspace_recovery as recovery
    monkeypatch.setattr(recovery,"run_repository_preflight",lambda *a,**k: R(""))
    out=supervisor.reconcile_abandoned_claims(wsroot,state,["g"],datetime(2026,1,1,tzinfo=UTC))
    assert out["g"]=="RECOVERY_PR_OPENED"
    assert seen and all(p==worker for p in seen)

def test_transport_recovery_rejects_recorded_workspace_outside_root(tmp_path):
    import json
    from datetime import UTC, datetime
    import scripts.continuous_flow_supervisor as supervisor
    wsroot=tmp_path/"ws"; wsroot.mkdir()
    state=tmp_path/"state"; state.mkdir()
    path=state/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{"g":{"state":"ABANDONED","recovery_state":"PENDING",
        "repository":"o/r","execution_stage":"COMMITTED","branch":"actor/x","head_sha":"head",
        "workspace_path":str(tmp_path.parent/"outside"),"recovery_attempts":0}}}))
    out=supervisor.reconcile_abandoned_claims(wsroot,state,["g"],datetime(2026,1,1,tzinfo=UTC))
    assert out["g"]=="RECOVERY_WORKSPACE_OUTSIDE_ROOT"

def test_stage_aware_recovery_hands_off_verified_pr_open(tmp_path,monkeypatch):
    import json
    from datetime import UTC, datetime
    import scripts.continuous_flow_supervisor as supervisor
    wsroot=tmp_path/"ws"; ws=wsroot/"r"; ws.mkdir(parents=True); state=tmp_path/"state"; state.mkdir()
    path=state/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{"g":{"state":"ABANDONED","repository":"o/r","execution_stage":"PR_OPEN","branch":"actor/autonomous-r-x","head_sha":"abc","pr_number":42,"recovery_attempts":0}}}))
    class R:
        returncode=0; stdout="abc refs/heads/actor/autonomous-r-x\n"; stderr=""
    def fake(cmd,*a,**k):
        if cmd[:3]==["gh","pr","view"]:
            return type("PR",(),{"returncode":0,"stdout":json.dumps({"number":42,"state":"OPEN","headRefOid":"abc"}),"stderr":""})()
        return R()
    monkeypatch.setattr(supervisor.subprocess,"run",fake)
    out=supervisor.reconcile_abandoned_claims(wsroot,state,["g"],datetime(2026,1,1,tzinfo=UTC))
    assert out["g"]=="RECOVERY_DURABLE_PR_HANDOFF"
    saved=json.loads(path.read_text())["grants"]["g"]
    assert saved["recovery_state"]=="RECOVERED"

def test_stage_aware_recovery_rejects_remote_head_drift(tmp_path,monkeypatch):
    import json
    from datetime import UTC, datetime
    import scripts.continuous_flow_supervisor as supervisor
    wsroot=tmp_path/"ws"; (wsroot/"r").mkdir(parents=True); state=tmp_path/"state"; state.mkdir()
    path=state/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{"g":{"state":"ABANDONED","repository":"o/r","execution_stage":"PUSHED","branch":"actor/autonomous-r-x","head_sha":"expected","recovery_attempts":0}}}))
    class R:
        returncode=0; stdout="other refs/heads/actor/autonomous-r-x\n"; stderr=""
    monkeypatch.setattr(supervisor.subprocess,"run",lambda *a,**k:R())
    out=supervisor.reconcile_abandoned_claims(wsroot,state,["g"],datetime(2026,1,1,tzinfo=UTC))
    assert out["g"]=="RECOVERY_DURABLE_REMOTE_HEAD_MISMATCH"
    assert json.loads(path.read_text())["grants"]["g"].get("recovery_state")!="RECOVERED"

def test_early_stage_clean_base_is_safe_no_product_work(tmp_path,monkeypatch):
    import json
    from datetime import UTC,datetime
    import scripts.continuous_flow_supervisor as supervisor
    wsroot=tmp_path/"ws"; (wsroot/"r").mkdir(parents=True); state=tmp_path/"state"; state.mkdir()
    path=state/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{"g":{"state":"ABANDONED","repository":"o/r","execution_stage":"BASE_VERIFIED","base_sha":"base","recovery_attempts":0}}}))
    class R:
        returncode=0; stderr=""
        def __init__(self,out): self.stdout=out
    def fake(cmd,**kwargs):
        if cmd[1:3]==["status","--porcelain"]: return R("")
        if cmd[1:3]==["rev-parse","HEAD"]: return R("base\n")
        if cmd[1:3]==["branch","--show-current"]: return R("actor-staging\n")
        return R("")
    monkeypatch.setattr(supervisor.subprocess,"run",fake)
    out=supervisor.reconcile_abandoned_claims(wsroot,state,["g"],datetime(2026,1,1,tzinfo=UTC))
    assert out["g"]=="RECOVERY_EARLY_STAGE_NO_PRODUCT_WORK"
    assert json.loads(path.read_text())["grants"]["g"]["recovery_state"]=="RECOVERED"

def test_early_stage_dirty_work_is_preserved_and_quarantined(tmp_path,monkeypatch):
    import json
    from datetime import UTC,datetime
    import scripts.continuous_flow_supervisor as supervisor
    wsroot=tmp_path/"ws"; (wsroot/"r").mkdir(parents=True); state=tmp_path/"state"; state.mkdir()
    path=state/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{"g":{"state":"ABANDONED","repository":"o/r","execution_stage":"BRANCH_CREATED","base_sha":"base","branch":"actor/autonomous-r-x","recovery_attempts":0}}}))
    class R:
        returncode=0; stderr=""
        def __init__(self,out): self.stdout=out
    def fake(cmd,**kwargs):
        if cmd[1:3]==["status","--porcelain"]: return R(" M src/x.py\n")
        if cmd[1:3]==["rev-parse","HEAD"]: return R("base\n")
        if cmd[1:3]==["branch","--show-current"]: return R("actor/autonomous-r-x\n")
        return R("")
    monkeypatch.setattr(supervisor.subprocess,"run",fake)
    out=supervisor.reconcile_abandoned_claims(wsroot,state,["g"],datetime(2026,1,1,tzinfo=UTC))
    assert out["g"]=="RECOVERY_EARLY_STAGE_DIRTY_PRESERVED"
    saved=json.loads(path.read_text())["grants"]["g"]
    assert saved["recovery_state"]=="QUARANTINED"
    assert saved["recovery_quarantine_reason"]=="UNCOMMITTED_WORK_REQUIRES_PRESERVATION"

def test_preserved_work_evidence_fingerprints_without_storing_diff(tmp_path,monkeypatch):
    import hashlib
    import scripts.continuous_flow_supervisor as supervisor
    class R:
        returncode=0; stderr=""
        def __init__(self,out): self.stdout=out
    def fake(cmd,**kwargs):
        if cmd[1:3]==["status","--porcelain"]: return R(" M src/x.py\n")
        if cmd[1:3]==["rev-parse","HEAD"]: return R("head\n")
        if cmd[1:3]==["branch","--show-current"]: return R("actor/x\n")
        if cmd[1:3]==["diff","--name-only"]: return R("src/x.py\n")
        if cmd[1:3]==["diff","--binary"]: return R("SECRET-DIFF-CONTENT")
        if cmd[1:3]==["ls-files","--others"]: return R("")
        raise AssertionError(cmd)
    monkeypatch.setattr(supervisor.subprocess,"run",fake)
    evidence=supervisor.preserved_work_evidence(tmp_path,"base")
    assert evidence["capture_status"]=="CAPTURED"
    assert evidence["branch"]=="actor/x"
    assert evidence["head_sha"]=="head"
    assert evidence["changed_paths"]==["src/x.py"]
    assert evidence["diff_sha256"]==hashlib.sha256(b"SECRET-DIFF-CONTENT").hexdigest()
    assert evidence["diff_bytes"]==len(b"SECRET-DIFF-CONTENT")
    assert "SECRET-DIFF-CONTENT" not in str(evidence)

def test_revalidate_preserved_work_requires_exact_identity(tmp_path,monkeypatch):
    import scripts.continuous_flow_supervisor as supervisor
    expected={"capture_status":"CAPTURED","branch":"actor/x","head_sha":"h","status_porcelain":" M x\n","changed_paths":["x"],"diff_sha256":"abc","diff_bytes":3}
    record={"base_sha":"b","preserved_work_evidence":expected}
    monkeypatch.setattr(supervisor,"preserved_work_evidence",lambda ws,base:dict(expected))
    assert supervisor.revalidate_preserved_work(tmp_path,record)=="PRESERVED_WORK_EXACT"
    changed=dict(expected); changed["diff_sha256"]="different"
    monkeypatch.setattr(supervisor,"preserved_work_evidence",lambda ws,base:changed)
    assert supervisor.revalidate_preserved_work(tmp_path,record)=="PRESERVED_WORK_CHANGED"

def test_preserved_quarantine_recheck_never_releases_work(tmp_path,monkeypatch):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    wsroot=tmp_path/"ws"; (wsroot/"r").mkdir(parents=True); state=tmp_path/"state"; state.mkdir()
    path=state/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{"g":{"state":"ABANDONED","recovery_state":"QUARANTINED","recovery_quarantine_reason":"UNCOMMITTED_WORK_REQUIRES_PRESERVATION","repository":"o/r","base_sha":"b","preserved_work_evidence":{"capture_status":"CAPTURED"}}}}))
    monkeypatch.setattr(supervisor,"revalidate_preserved_work",lambda ws,record:"PRESERVED_WORK_EXACT")
    monkeypatch.setattr(supervisor,"validate_preserved_work",lambda ws,record:"PRESERVED_VALIDATION_PASSED")
    assert supervisor.recheck_preserved_work_quarantines(wsroot,state)=={"g":"PRESERVED_VALIDATION_PASSED"}
    saved=json.loads(path.read_text())["grants"]["g"]
    assert saved["recovery_state"]=="QUARANTINED"
    assert saved["preserved_work_revalidation"]=="PRESERVED_WORK_EXACT"
    assert saved["preserved_work_validation"]=="PRESERVED_VALIDATION_PASSED"

def test_validate_preserved_work_requires_identity_before_and_after(tmp_path,monkeypatch):
    import scripts.continuous_flow_supervisor as supervisor
    (tmp_path/"engineering").mkdir(); (tmp_path/"engineering"/"preflight.py").write_text("pass")
    record={"preserved_work_evidence":{"diff_sha256":"abc"}}
    checks=iter(["PRESERVED_WORK_EXACT","PRESERVED_WORK_EXACT"])
    monkeypatch.setattr(supervisor,"revalidate_preserved_work",lambda ws,r:next(checks))
    class R: returncode=0; stdout=""; stderr=""
    monkeypatch.setattr(supervisor.subprocess,"run",lambda *a,**k:R())
    assert supervisor.validate_preserved_work(tmp_path,record)=="PRESERVED_VALIDATION_PASSED"

def test_validate_preserved_work_detects_validator_mutation(tmp_path,monkeypatch):
    import scripts.continuous_flow_supervisor as supervisor
    (tmp_path/"engineering").mkdir(); (tmp_path/"engineering"/"preflight.py").write_text("pass")
    record={"preserved_work_evidence":{"diff_sha256":"abc"}}
    checks=iter(["PRESERVED_WORK_EXACT","PRESERVED_WORK_CHANGED"])
    monkeypatch.setattr(supervisor,"revalidate_preserved_work",lambda ws,r:next(checks))
    class R: returncode=0; stdout=""; stderr=""
    monkeypatch.setattr(supervisor.subprocess,"run",lambda *a,**k:R())
    assert supervisor.validate_preserved_work(tmp_path,record)=="PRESERVED_VALIDATION_MUTATED_WORKSPACE"

def test_validate_preserved_work_does_not_run_preflight_without_identity(tmp_path,monkeypatch):
    import scripts.continuous_flow_supervisor as supervisor
    record={"preserved_work_evidence":{"diff_sha256":"abc"}}
    monkeypatch.setattr(supervisor,"revalidate_preserved_work",lambda ws,r:"PRESERVED_WORK_CHANGED")
    monkeypatch.setattr(supervisor.subprocess,"run",lambda *a,**k:(_ for _ in ()).throw(AssertionError("must not execute")))
    assert supervisor.validate_preserved_work(tmp_path,record)=="PRESERVED_VALIDATION_IDENTITY_PRECHECK_FAILED"

def test_preserved_validation_prefers_canonical_engineering_preflight(tmp_path,monkeypatch):
    import scripts.continuous_flow_supervisor as supervisor
    engineering=tmp_path/"engineering"; engineering.mkdir(); (engineering/"preflight.py").write_text("pass")
    scripts=tmp_path/"scripts"; scripts.mkdir(); (scripts/"preflight.py").write_text("pass")
    record={"preserved_work_evidence":{"diff_sha256":"abc"}}
    checks=iter(["PRESERVED_WORK_EXACT","PRESERVED_WORK_EXACT"])
    monkeypatch.setattr(supervisor,"revalidate_preserved_work",lambda ws,r:next(checks))
    seen=[]
    class R: returncode=0; stdout=""; stderr=""
    def fake(argv,**kwargs): seen.append(argv); return R()
    monkeypatch.setattr(supervisor.subprocess,"run",fake)
    assert supervisor.validate_preserved_work(tmp_path,record)=="PRESERVED_VALIDATION_PASSED"
    assert seen[0][1].endswith("engineering\\preflight.py") or seen[0][1].endswith("engineering/preflight.py")
    assert seen[0][2]=="--evidence"
    assert seen[0][3].endswith("preflight-evidence.json")


def test_preserved_validation_fails_closed_without_repository_preflight(tmp_path,monkeypatch):
    import scripts.continuous_flow_supervisor as supervisor
    record={"preserved_work_evidence":{"diff_sha256":"abc"}}
    monkeypatch.setattr(supervisor,"revalidate_preserved_work",lambda ws,r:"PRESERVED_WORK_EXACT")
    monkeypatch.setattr(supervisor.subprocess,"run",lambda *a,**k:(_ for _ in ()).throw(AssertionError("must not execute")))
    assert supervisor.validate_preserved_work(tmp_path,record)=="PRESERVED_VALIDATION_PREFLIGHT_UNAVAILABLE"


def test_recovery_capability_is_exact_bound_and_single_use(tmp_path,monkeypatch):
    import scripts.continuous_flow_supervisor as supervisor
    record={"repository":"o/r","base_sha":"base","branch":"actor/x","preserved_work_validation":"PRESERVED_VALIDATION_PASSED",
            "preserved_work_evidence":{"diff_sha256":"diff","head_sha":"base","repair_eligible":True}}
    cap=supervisor.issue_preserved_recovery_capability(record)
    assert cap and cap["state"]=="ISSUED" and cap["preserved_diff_sha256"]=="diff"
    record["preserved_recovery_capability"]=cap
    monkeypatch.setattr(supervisor,"revalidate_preserved_work",lambda ws,r:"PRESERVED_WORK_EXACT")
    assert supervisor.consume_preserved_recovery_capability(tmp_path,record)=="RECOVERY_CAPABILITY_CONSUMED"
    assert cap["state"]=="CONSUMED"
    assert supervisor.consume_preserved_recovery_capability(tmp_path,record)=="RECOVERY_CAPABILITY_UNAVAILABLE"

def test_recovery_capability_rejects_binding_or_identity_drift(tmp_path,monkeypatch):
    import scripts.continuous_flow_supervisor as supervisor
    record={"repository":"o/r","base_sha":"base","branch":"actor/x","preserved_work_validation":"PRESERVED_VALIDATION_PASSED",
            "preserved_work_evidence":{"diff_sha256":"diff","head_sha":"base","repair_eligible":True}}
    record["preserved_recovery_capability"]=supervisor.issue_preserved_recovery_capability(record)
    record["branch"]="actor/changed"
    assert supervisor.consume_preserved_recovery_capability(tmp_path,record)=="RECOVERY_CAPABILITY_BINDING_MISMATCH"
    record["branch"]="actor/x"
    monkeypatch.setattr(supervisor,"revalidate_preserved_work",lambda ws,r:"PRESERVED_WORK_CHANGED")
    assert supervisor.consume_preserved_recovery_capability(tmp_path,record)=="RECOVERY_CAPABILITY_IDENTITY_CHANGED"
    assert record["preserved_recovery_capability"]["state"]=="ISSUED"

def test_commit_preserved_work_advances_only_after_exact_commit_fingerprint(tmp_path,monkeypatch):
    import hashlib
    import scripts.continuous_flow_supervisor as supervisor
    diff="validated-diff"
    record={"repository":"o/r","base_sha":"base","branch":"actor/x","preserved_work_validation":"PRESERVED_VALIDATION_PASSED",
            "preserved_work_evidence":{"diff_sha256":hashlib.sha256(diff.encode()).hexdigest(),"head_sha":"base"},
            "preserved_recovery_capability":{"state":"ISSUED"}}
    monkeypatch.setattr(supervisor,"consume_preserved_recovery_capability",lambda ws,r:"RECOVERY_CAPABILITY_CONSUMED")
    class R:
        returncode=0
        def __init__(self,out=""): self.stdout=out; self.stderr=""
    def fake(cmd,**kwargs):
        if cmd[:3]==["git","rev-parse","HEAD"]: return R("newhead\n")
        if cmd[:3]==["git","branch","--show-current"]: return R("actor/x\n")
        if cmd[:2]==["git","diff"]: return R(diff)
        return R()
    monkeypatch.setattr(supervisor.subprocess,"run",fake)
    assert supervisor.commit_preserved_work(tmp_path,record)=="PRESERVED_COMMIT_RECOVERED"
    assert record["execution_stage"]=="COMMITTED"
    assert record["head_sha"]=="newhead"
    assert record["recovery_state"]=="RECOVERING"

def test_commit_preserved_work_rejects_commit_fingerprint_drift(tmp_path,monkeypatch):
    import scripts.continuous_flow_supervisor as supervisor
    record={"base_sha":"base","branch":"actor/x","preserved_work_evidence":{"diff_sha256":"expected"},
            "preserved_recovery_capability":{"state":"ISSUED"}}
    monkeypatch.setattr(supervisor,"consume_preserved_recovery_capability",lambda ws,r:"RECOVERY_CAPABILITY_CONSUMED")
    class R:
        returncode=0
        def __init__(self,out=""): self.stdout=out; self.stderr=""
    def fake(cmd,**kwargs):
        if cmd[:3]==["git","rev-parse","HEAD"]: return R("newhead\n")
        if cmd[:3]==["git","branch","--show-current"]: return R("actor/x\n")
        if cmd[:2]==["git","diff"]: return R("different")
        return R()
    monkeypatch.setattr(supervisor.subprocess,"run",fake)
    assert supervisor.commit_preserved_work(tmp_path,record)=="PRESERVED_COMMIT_FINGERPRINT_MISMATCH"
    assert "execution_stage" not in record

def test_consumed_recovery_capability_records_terminal_mutation_failure(tmp_path,monkeypatch):
    import scripts.continuous_flow_supervisor as supervisor
    record={"base_sha":"base","branch":"actor/x","preserved_work_evidence":{"diff_sha256":"x"},
            "preserved_recovery_capability":{"state":"CONSUMED"}}
    monkeypatch.setattr(supervisor,"consume_preserved_recovery_capability",lambda ws,r:"RECOVERY_CAPABILITY_CONSUMED")
    class R:
        def __init__(self,code=0,out=""): self.returncode=code; self.stdout=out; self.stderr=""
    monkeypatch.setattr(supervisor.subprocess,"run",lambda cmd,**kwargs:R(1) if cmd[:2]==["git","add"] else R())
    assert supervisor.commit_preserved_work(tmp_path,record)=="PRESERVED_COMMIT_STAGE_FAILED"
    assert record["preserved_recovery_capability"]["state"]=="CONSUMED"
    assert record["preserved_recovery_capability"]["outcome"]=="STAGE_FAILED"

def test_exact_recovery_commit_records_terminal_success(tmp_path,monkeypatch):
    import hashlib
    import scripts.continuous_flow_supervisor as supervisor
    diff="d"
    record={"base_sha":"base","branch":"actor/x","preserved_work_evidence":{"diff_sha256":hashlib.sha256(diff.encode()).hexdigest()},
            "preserved_recovery_capability":{"state":"CONSUMED"}}
    monkeypatch.setattr(supervisor,"consume_preserved_recovery_capability",lambda ws,r:"RECOVERY_CAPABILITY_CONSUMED")
    class R:
        returncode=0
        def __init__(self,out=""): self.stdout=out; self.stderr=""
    def fake(cmd,**kwargs):
        if cmd[:3]==["git","rev-parse","HEAD"]: return R("h\n")
        if cmd[:3]==["git","branch","--show-current"]: return R("actor/x\n")
        if cmd[:2]==["git","diff"]: return R(diff)
        return R()
    monkeypatch.setattr(supervisor.subprocess,"run",fake)
    assert supervisor.commit_preserved_work(tmp_path,record)=="PRESERVED_COMMIT_RECOVERED"
    assert record["preserved_recovery_capability"]["outcome"]=="COMMITTED_EXACT"

def test_reconcile_consumed_recovery_adopts_only_exact_clean_descendant(tmp_path,monkeypatch):
    import hashlib
    import scripts.continuous_flow_supervisor as supervisor
    diff="exact"
    record={"base_sha":"base","branch":"actor/x","preserved_work_evidence":{"diff_sha256":hashlib.sha256(diff.encode()).hexdigest()},
            "preserved_recovery_capability":{"state":"CONSUMED","outcome":"MUTATION_STARTED"}}
    class R:
        def __init__(self,code=0,out=""): self.returncode=code; self.stdout=out; self.stderr=""
    def fake(cmd,**kwargs):
        if cmd[1:3]==["status","--porcelain"]: return R(0,"")
        if cmd[1:3]==["rev-parse","HEAD"]: return R(0,"new\n")
        if cmd[1:3]==["branch","--show-current"]: return R(0,"actor/x\n")
        if cmd[1:3]==["merge-base","--is-ancestor"]: return R()
        if cmd[1:3]==["diff","--binary"]: return R(0,diff)
        raise AssertionError(cmd)
    monkeypatch.setattr(supervisor.subprocess,"run",fake)
    assert supervisor.reconcile_consumed_recovery(tmp_path,record)=="CONSUMED_RECOVERY_COMMIT_ADOPTED"
    assert record["execution_stage"]=="COMMITTED" and record["head_sha"]=="new"
    assert record["preserved_recovery_capability"]["outcome"]=="COMMITTED_EXACT_RECONCILED"

def test_reconcile_consumed_recovery_refuses_dirty_or_wrong_commit(tmp_path,monkeypatch):
    import scripts.continuous_flow_supervisor as supervisor
    record={"base_sha":"base","branch":"actor/x","preserved_work_evidence":{"diff_sha256":"expected"},
            "preserved_recovery_capability":{"state":"CONSUMED"}}
    class R:
        def __init__(self,code=0,out=""): self.returncode=code; self.stdout=out; self.stderr=""
    def dirty(cmd,**kwargs):
        if cmd[1:3]==["status","--porcelain"]: return R(0," M x\n")
        if cmd[1:3]==["rev-parse","HEAD"]: return R(0,"new\n")
        if cmd[1:3]==["branch","--show-current"]: return R(0,"actor/x\n")
        raise AssertionError(cmd)
    monkeypatch.setattr(supervisor.subprocess,"run",dirty)
    assert supervisor.reconcile_consumed_recovery(tmp_path,record)=="CONSUMED_RECOVERY_DIRTY_AFTER_COMMIT"
    assert "execution_stage" not in record

def test_reconcile_consumed_staged_work_requires_exact_index_and_clean_worktree(tmp_path,monkeypatch):
    import hashlib
    import scripts.continuous_flow_supervisor as supervisor
    diff="exact"
    record={"base_sha":"base","branch":"actor/x","preserved_work_evidence":{"diff_sha256":hashlib.sha256(diff.encode()).hexdigest()},
            "preserved_recovery_capability":{"state":"CONSUMED","outcome":"MUTATION_STARTED"}}
    class R:
        def __init__(self,code=0,out=""): self.returncode=code; self.stdout=out; self.stderr=""
    def fake(cmd,**kwargs):
        if cmd[1:3]==["rev-parse","HEAD"]: return R(0,"base\n")
        if cmd[1:3]==["branch","--show-current"]: return R(0,"actor/x\n")
        if cmd[1:4]==["diff","--cached","--binary"]: return R(0,diff)
        if cmd[1:3]==["diff","--binary"] and len(cmd)>3: return R(0,diff)
        if cmd[1:3]==["diff","--binary"]: return R(0,"")
        raise AssertionError(cmd)
    monkeypatch.setattr(supervisor.subprocess,"run",fake)
    assert supervisor.reconcile_consumed_staged_work(tmp_path,record)=="CONSUMED_RECOVERY_EXACT_STAGED"
    assert record["recovery_state"]=="QUARANTINED"
    assert record["preserved_recovery_capability"]["state"]=="CONSUMED"
    assert record["preserved_recovery_capability"]["outcome"]=="EXACT_STAGED_RECONCILED"

def test_reconcile_consumed_staged_work_refuses_unstaged_drift(tmp_path,monkeypatch):
    import hashlib
    import scripts.continuous_flow_supervisor as supervisor
    diff="exact"
    record={"base_sha":"base","branch":"actor/x","preserved_work_evidence":{"diff_sha256":hashlib.sha256(diff.encode()).hexdigest()},
            "preserved_recovery_capability":{"state":"CONSUMED"}}
    class R:
        def __init__(self,out=""): self.returncode=0; self.stdout=out; self.stderr=""
    def fake(cmd,**kwargs):
        if cmd[1:3]==["rev-parse","HEAD"]: return R("base\n")
        if cmd[1:3]==["branch","--show-current"]: return R("actor/x\n")
        if cmd[1:4]==["diff","--cached","--binary"]: return R(diff)
        if cmd[1:3]==["diff","--binary"] and len(cmd)>3: return R(diff)
        if cmd[1:3]==["diff","--binary"]: return R("drift")
        raise AssertionError(cmd)
    monkeypatch.setattr(supervisor.subprocess,"run",fake)
    assert supervisor.reconcile_consumed_staged_work(tmp_path,record)=="STAGED_RECOVERY_NOT_EXACTLY_STAGED"
    assert "recovery_result" not in record

def test_staged_recovery_replacement_is_commit_only_and_parent_bound(tmp_path,monkeypatch):
    import hashlib
    import scripts.continuous_flow_supervisor as supervisor
    diff="exact"; digest=hashlib.sha256(diff.encode()).hexdigest()
    record={"repository":"o/r","base_sha":"base","branch":"actor/x","recovery_result":"CONSUMED_RECOVERY_EXACT_STAGED",
            "preserved_work_evidence":{"diff_sha256":digest},
            "preserved_recovery_capability":{"capability_id":"old","state":"CONSUMED","outcome":"EXACT_STAGED_RECONCILED"}}
    cap=supervisor.issue_staged_commit_capability(record)
    assert cap["scope"]=="COMMIT_ONLY" and cap["parent_capability_id"]=="old"
    record["staged_commit_capability"]=cap
    monkeypatch.setattr(supervisor,"reconcile_consumed_staged_work",lambda ws,r:"CONSUMED_RECOVERY_EXACT_STAGED")
    class R:
        returncode=0
        def __init__(self,out=""): self.stdout=out; self.stderr=""
    calls=[]
    def fake(cmd,**kwargs):
        calls.append(cmd)
        if cmd[:3]==["git","rev-parse","HEAD"]: return R("new\n")
        if cmd[:3]==["git","branch","--show-current"]: return R("actor/x\n")
        if cmd[:2]==["git","diff"]: return R(diff)
        return R()
    monkeypatch.setattr(supervisor.subprocess,"run",fake)
    assert supervisor.commit_reconciled_staged_work(tmp_path,record)=="STAGED_COMMIT_RECOVERED"
    assert not any(cmd[:2]==["git","add"] for cmd in calls)
    assert record["execution_stage"]=="COMMITTED" and cap["state"]=="CONSUMED"

def test_staged_commit_capability_refuses_parent_binding_drift(tmp_path):
    import scripts.continuous_flow_supervisor as supervisor
    record={"repository":"o/r","base_sha":"b","branch":"x","preserved_work_evidence":{"diff_sha256":"d"},
            "preserved_recovery_capability":{"capability_id":"old","state":"CONSUMED"},
            "staged_commit_capability":{"capability_id":"new","state":"ISSUED","scope":"COMMIT_ONLY","parent_capability_id":"other",
                "repository":"o/r","base_sha":"b","branch":"x","preserved_diff_sha256":"d"}}
    assert supervisor.commit_reconciled_staged_work(tmp_path,record)=="STAGED_COMMIT_CAPABILITY_BINDING_MISMATCH"
    assert record["staged_commit_capability"]["state"]=="ISSUED"

def test_pending_recovery_requeues_exact_recovered_commit_from_quarantine(tmp_path):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    state=tmp_path/"state"; state.mkdir()
    path=state/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{"g":{"state":"ABANDONED","recovery_state":"QUARANTINED",
        "execution_stage":"COMMITTED","head_sha":"abc","recovery_result":"STAGED_COMMIT_RECOVERED"}}}))
    assert supervisor.pending_recovery_grants(state)==["g"]
    saved=json.loads(path.read_text())["grants"]["g"]
    assert saved["recovery_state"]=="RECOVERING"

def test_pending_recovery_does_not_requeue_ambiguous_quarantine(tmp_path):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    state=tmp_path/"state"; state.mkdir()
    (state/"hands-off-grant-lifecycle.json").write_text(json.dumps({"grants":{"g":{"state":"ABANDONED",
        "recovery_state":"QUARANTINED","execution_stage":"COMMITTED","head_sha":"abc",
        "recovery_result":"PRESERVED_COMMIT_FINGERPRINT_MISMATCH"}}}))
    assert supervisor.pending_recovery_grants(state)==[]


def test_user_active_liveness_reacquire_retries_with_bounded_delay():
    from scripts.continuous_flow_supervisor import continuation_delay
    delay=continuation_delay(lifecycle_advanced=False,
        liveness_reacquire={"requested":True,"reason":"ELIGIBLE_IDLE"},
        builder_ok=True,builder_status="USER_ACTIVE",normal_seconds=300)
    assert delay==300


def test_user_active_liveness_reacquire_never_hot_loops():
    from scripts.continuous_flow_supervisor import continuation_delay
    delay=continuation_delay(lifecycle_advanced=False,
        liveness_reacquire={"requested":True,"reason":"ELIGIBLE_IDLE"},
        builder_ok=True,builder_status="USER_ACTIVE",normal_seconds=10)
    assert delay==60


def test_supervisor_registers_merge_watcher_cleanup():
    from pathlib import Path
    source = Path('scripts/continuous_flow_supervisor.py').read_text(encoding='utf-8')
    assert 'atexit.register(cleanup_merge_watcher)' in source
    assert 'merge.wait(timeout=5)' in source


def test_existing_preserved_aider_artifact_quarantine_is_released(tmp_path,monkeypatch):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    wsroot=tmp_path/"ws"; (wsroot/"r").mkdir(parents=True); state=tmp_path/"state"; state.mkdir()
    evidence={"capture_status":"CAPTURED","diff_bytes":0,"changed_paths":[],
              "untracked":[{"path":".aider.chat.history.md","kind":"file"}]}
    record={"state":"ABANDONED","repository":"o/r","execution_stage":"BRANCH_CREATED",
            "recovery_state":"QUARANTINED","recovery_quarantine_reason":"UNCOMMITTED_WORK_REQUIRES_PRESERVATION",
            "preserved_work_evidence":evidence}
    path=state/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{"g":record}}))
    monkeypatch.setattr(supervisor,"revalidate_preserved_work",lambda ws,record:"PRESERVED_WORK_EXACT")
    monkeypatch.setattr(supervisor,"validate_preserved_work",lambda *args: (_ for _ in ()).throw(AssertionError("must not validate transient artifacts")))
    out=supervisor.recheck_preserved_work_quarantines(wsroot,state)
    assert out=={"g":"PRESERVED_BUILDER_ARTIFACTS_RELEASED"}
    saved=json.loads(path.read_text())["grants"]["g"]
    assert saved["recovery_state"]=="RECOVERED"
    assert saved["ignored_builder_artifacts"]==[".aider.chat.history.md"]


def test_early_stage_aider_artifacts_do_not_quarantine_dead_worker(tmp_path,monkeypatch):
    import json
    from datetime import UTC,datetime
    import scripts.continuous_flow_supervisor as supervisor
    wsroot=tmp_path/"ws"; ws=(wsroot/"r"); ws.mkdir(parents=True); state=tmp_path/"state"; state.mkdir()
    (ws/".aider.chat.history.md").write_text("transient")
    path=state/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{"g":{"state":"ABANDONED","repository":"o/r","execution_stage":"BRANCH_CREATED","base_sha":"base","branch":"actor/autonomous-r-x","recovery_attempts":0}}}))
    class R:
        returncode=0; stderr=""
        def __init__(self,out): self.stdout=out
    def fake(cmd,**kwargs):
        if cmd[1:3]==["status","--porcelain"]: return R("?? .aider.chat.history.md\n")
        if cmd[1:3]==["rev-parse","HEAD"]: return R("base\n")
        if cmd[1:3]==["branch","--show-current"]: return R("actor/autonomous-r-x\n")
        if cmd[1:3] in (["diff","--name-only"],["diff","--binary"]): return R("")
        if cmd[1:3]==["ls-files","--others"]: return R(".aider.chat.history.md\0")
        return R("")
    monkeypatch.setattr(supervisor.subprocess,"run",fake)
    out=supervisor.reconcile_abandoned_claims(wsroot,state,["g"],datetime(2026,1,1,tzinfo=UTC))
    assert out["g"]=="RECOVERY_EARLY_STAGE_BUILDER_ARTIFACTS_ONLY"
    saved=json.loads(path.read_text())["grants"]["g"]
    assert saved["recovery_state"]=="RECOVERED"
    assert saved["ignored_builder_artifacts"]==[".aider.chat.history.md"]


def test_product_convergence_flags_unexplained_authorized_product(tmp_path):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    state=tmp_path/"state"; state.mkdir()
    (state/"hands-off-grant-lifecycle.json").write_text(json.dumps({"grants":{}}))
    (state/"dispatch-decision.json").write_text(json.dumps({"decisions":[]}))
    result=supervisor.product_convergence(state,["knowledge-engine","rocksmith","everward"])
    assert result["status"]=="UNEXPLAINED_ELIGIBLE_IDLE"
    assert result["unexplained_eligible_products"]==["everward","knowledge-engine","rocksmith"]



def test_product_convergence_accepts_identity_bearing_live_worker(tmp_path):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    state=tmp_path/"state"; state.mkdir()
    (state/"hands-off-grant-lifecycle.json").write_text(json.dumps({"grants":{}}))
    grants=state/"hands-off-execution-grants"; grants.mkdir()
    (grants/"g-live.json").write_text(json.dumps({
        "repository":"jweter/VoiceStudio",
        "execution_slice":{"repository":"jweter/VoiceStudio"},
    }))
    worker=state/"worker-state"/"voicestudio-worker-1"; worker.mkdir(parents=True)
    (worker/"builder.json").write_text(json.dumps({
        "status":"BUILDER_RUNNING","worker_id":"voicestudio-worker-1",
        "grant_id":"g-live","invocation_id":"inv-live",
        "worker":{"pid":3156,"process_start_fingerprint":"windows:123"},
    }))
    result=supervisor.product_convergence(state,["voicestudio"])
    assert result["status"]=="CONVERGED"
    assert result["active_products"]["voicestudio"]["state"]=="BUILDER_RUNNING"
    assert result["active_products"]["voicestudio"]["invocation_id"]=="inv-live"


def test_product_convergence_rejects_incomplete_live_worker_identity(tmp_path):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    state=tmp_path/"state"; state.mkdir()
    (state/"hands-off-grant-lifecycle.json").write_text(json.dumps({"grants":{}}))
    grants=state/"hands-off-execution-grants"; grants.mkdir()
    (grants/"g-live.json").write_text(json.dumps({"repository":"jweter/VoiceStudio"}))
    worker=state/"worker-state"/"voicestudio-worker-1"; worker.mkdir(parents=True)
    (worker/"builder.json").write_text(json.dumps({
        "status":"BUILDER_RUNNING","worker_id":"voicestudio-worker-1",
        "grant_id":"g-live","invocation_id":"inv-live","worker":{"pid":3156},
    }))
    result=supervisor.product_convergence(state,["voicestudio"])
    assert result["status"]=="UNEXPLAINED_ELIGIBLE_IDLE"
    assert result["unexplained_eligible_products"]==["voicestudio"]


def test_product_convergence_resolves_issued_repository_from_execution_slice(tmp_path):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    state=tmp_path/"state"; state.mkdir()
    grants={"g1":{"state":"ISSUED","authorized_repositories":["jweter/rocksmith-cdlc-generator"],
                  "execution_slice":{"repository":"jweter/rocksmith-cdlc-generator",
                                     "slice_id":"human-only-product-reality-debt-report-v1"}}}
    (state/"hands-off-grant-lifecycle.json").write_text(json.dumps({"grants":grants}))
    result=supervisor.product_convergence(state,["rocksmith"])
    assert result["status"]=="CONVERGED"
    assert result["unexplained_eligible_products"]==[]
    assert result["active_products"]["rocksmith"]=={
        "grant_id":"g1","repository":"jweter/rocksmith-cdlc-generator","state":"ISSUED"
    }


def test_product_convergence_accepts_active_or_explicit_reason(tmp_path):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    state=tmp_path/"state"; state.mkdir()
    grants={"g1":{"state":"CLAIMED","repository":"jweter/rocksmith-cdlc-generator"},
            "g2":{"state":"RUNNING","repository":"jweter/Project-Everward"}}
    (state/"hands-off-grant-lifecycle.json").write_text(json.dumps({"grants":grants}))
    decisions=[{"repository":"jweter/knowledge-engine-core","product":"knowledge-engine",
                "outcome":"NOT_READY","reason":"EXECUTION_SLICE_NOT_READY"}]
    (state/"dispatch-decision.json").write_text(json.dumps({"invocation_id":"current","decisions":decisions}))
    result=supervisor.product_convergence(
        state,["knowledge-engine","rocksmith","everward"],current_invocation_id="current")
    assert result["status"]=="CONVERGED"
    assert sorted(result["active_products"])==["everward","rocksmith"]
    assert result["explained_nonexecuting_products"]["knowledge-engine"][0]["outcome"]=="NOT_READY"


def test_dispatch_decision_reason_codes_include_already_owned(tmp_path,monkeypatch):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    state=tmp_path/"state"; state.mkdir()
    activation={"products":{"knowledge-engine":{"repositories":{"jweter/knowledge-engine-core":"abc"}}}}
    monkeypatch.setattr(supervisor,"verify_activation_for_dispatch",lambda *args:None)
    monkeypatch.setattr(supervisor,"load_execution_slice",lambda *args:{"slice_id":"s","repository":"jweter/knowledge-engine-core","exact_head":"abc"})
    monkeypatch.setattr(supervisor,"conflicting_nonterminal_grant",lambda *args:{"grant_id":"g","state":"CLAIMED"})
    try:
        supervisor.select_execution_target(state,activation,["knowledge-engine"])
    except RuntimeError as exc:
        assert "ALREADY_OWNED:g" in str(exc)
    else:
        raise AssertionError("owned lane must not be selected twice")
    evidence=json.loads((state/"dispatch-decision.json").read_text())
    assert evidence["decisions"][0]["outcome"]=="ALREADY_OWNED"


def test_product_convergence_rejects_stale_dispatch_invocation(tmp_path):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    state=tmp_path/"state"; state.mkdir()
    (state/"hands-off-grant-lifecycle.json").write_text(json.dumps({"grants":{}}))
    decision={"invocation_id":"old","decisions":[{"repository":"jweter/Project-Everward","product":"everward","outcome":"NOT_READY","reason":"OLD"}]}
    (state/"dispatch-decision.json").write_text(json.dumps(decision))
    result=supervisor.product_convergence(state,["everward"],current_invocation_id="new")
    assert result["status"]=="UNEXPLAINED_ELIGIBLE_IDLE"
    assert result["dispatch_evidence_fresh"] is False
    assert result["explained_nonexecuting_products"]=={}


def test_product_convergence_rejects_stale_parallel_block_evidence(tmp_path):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    state=tmp_path/"state"; state.mkdir()
    (state/"hands-off-grant-lifecycle.json").write_text(json.dumps({"grants":{}}))
    (state/"dispatch-blocks.json").write_text(json.dumps({
        "invocation_id":"old","blocked":[{"product":"everward","reason":"NO_ELIGIBLE_RUNNER"}]
    }))
    result=supervisor.product_convergence(
        state,["everward"],current_invocation_id="current")
    assert result["status"]=="UNEXPLAINED_ELIGIBLE_IDLE"
    assert result["dispatch_block_evidence_fresh"] is False
    assert result["explained_nonexecuting_products"]=={}


def test_parallel_dispatch_records_current_empty_batch_reason(tmp_path,monkeypatch):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    from project_orchestrator.parallel_worker_pool import ParallelWorkerPool
    from tests.test_parallel_worker_pool import ManualExecutor
    pool=ParallelWorkerPool(max_workers_per_product=1,max_total_workers=1,
                            executor_factory=ManualExecutor)
    monkeypatch.setattr(supervisor,"ready_task_batch",lambda *args:[])
    workers=supervisor.dispatch_parallel_builders(
        pool,tmp_path,tmp_path,tmp_path,["everward"],dispatch_invocation_id="cycle-1")
    assert workers==[]
    blocks=json.loads((tmp_path/"dispatch-blocks.json").read_text())
    assert blocks["invocation_id"]=="cycle-1"
    assert blocks["blocked"]==[{
        "product":"everward","worker_id":None,"repository":None,
        "slice_id":None,"reason":"NO_CONFLICT_FREE_READY_SLICE"
    }]
    convergence=supervisor.product_convergence(
        tmp_path,["everward"],current_invocation_id="cycle-1")
    assert convergence["status"]=="CONVERGED"
    assert convergence["dispatch_block_evidence_fresh"] is True
    assert convergence["explained_nonexecuting_products"]["everward"]["reason"]=="NO_CONFLICT_FREE_READY_SLICE"


def test_parallel_dispatch_preserves_direction_required_without_false_p1(tmp_path,monkeypatch):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    from project_orchestrator.parallel_worker_pool import ParallelWorkerPool
    from tests.test_parallel_worker_pool import ManualExecutor
    activation={"products":{"everward":{"repositories":{"jweter/Project-Everward":"abc"}}}}
    (tmp_path/"hands-off-activation.json").write_text(json.dumps(activation))
    requests=tmp_path/"direction-requests"; requests.mkdir()
    (requests/"jweter__Project-Everward.json").write_text(json.dumps({
        "status":"DIRECTION_REQUIRED","repository":"jweter/Project-Everward",
        "exact_head":"abc","reason":"CAPABILITY_GRAPH_REQUIRED"
    }))
    pool=ParallelWorkerPool(max_workers_per_product=1,max_total_workers=1,
                            executor_factory=ManualExecutor)
    monkeypatch.setattr(supervisor,"ready_task_batch",lambda *args:[])
    workers=supervisor.dispatch_parallel_builders(
        pool,tmp_path,tmp_path,tmp_path,["everward"],dispatch_invocation_id="cycle-2")
    assert workers==[]
    blocks=json.loads((tmp_path/"dispatch-blocks.json").read_text())
    assert blocks["blocked"]==[{
        "product":"everward","worker_id":None,"repository":"jweter/Project-Everward",
        "slice_id":None,"reason":"DIRECTION_REQUIRED","detail_reason":"CAPABILITY_GRAPH_REQUIRED"
    }]
    from project_orchestrator.planning_dispatch import record_eligible_idle_streaks
    streaks=record_eligible_idle_streaks(
        tmp_path,["everward"],current_invocation_id="cycle-2")
    assert streaks["products"]["everward"]["reason"]=="DIRECTION_REQUIRED"
    assert streaks["products"]["everward"]["streak"]==0
    assert streaks["products"]["everward"]["p1_liveness_defect"] is False


def test_product_convergence_accepts_matching_dispatch_invocation(tmp_path):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    state=tmp_path/"state"; state.mkdir()
    (state/"hands-off-grant-lifecycle.json").write_text(json.dumps({"grants":{}}))
    decision={"invocation_id":"same","decisions":[{"repository":"jweter/Project-Everward","product":"everward","outcome":"NOT_READY","reason":"CURRENT"}]}
    (state/"dispatch-decision.json").write_text(json.dumps(decision))
    result=supervisor.product_convergence(state,["everward"],current_invocation_id="same")
    assert result["status"]=="CONVERGED"
    assert result["dispatch_evidence_fresh"] is True


def test_decision_learning_hint_is_advisory(tmp_path):
    supervisor.atomic(tmp_path / "decision-learning.json", {
        "schema_version": 1,
        "learning": {
            "policies": [{
                "decision_class": "portfolio:allocation",
                "state": "VERIFIED_ADVISORY_EVIDENCE",
                "verified_samples": 3,
                "preferred_selector": "JEV_SHADOW",
            }]
        },
    })
    hint = supervisor.decision_learning_hint(tmp_path, "portfolio:allocation")
    assert hint["preferred_selector"] == "JEV_SHADOW"
    assert hint["authority"] == "ADVISORY_ONLY"
    assert hint["may_change_legal_candidate_set"] is False
    assert hint["may_bypass_gates"] is False


def test_refresh_decision_learning_writes_empty_safe_state(tmp_path):
    payload = supervisor.refresh_decision_learning(tmp_path)
    assert payload["attributed_outcomes"] == 0
    assert payload["learning"]["authority"] == "ADVISORY_ONLY"
    assert payload["learning"]["automatic_promotion"] is False
    assert (tmp_path / "decision-learning.json").exists()


# Decision-learning supervisor integration uses the module namespace explicitly.
import scripts.continuous_flow_supervisor as supervisor


def test_parallel_dispatch_assigns_independent_product_slots(tmp_path, monkeypatch):
    import scripts.continuous_flow_supervisor as supervisor
    from project_orchestrator.parallel_worker_pool import ParallelWorkerPool
    from tests.test_parallel_worker_pool import ManualExecutor
    pool=ParallelWorkerPool(max_workers_per_product=1,max_total_workers=4,executor_factory=ManualExecutor)
    monkeypatch.setattr(supervisor,"RUNTIME_LEASES",
                        supervisor.RuntimeLeasePool(supervisor.DEFAULT_RUNTIME_CAPACITIES))
    monkeypatch.setattr(supervisor,"ready_task_batch",lambda state,activation,product,limit:[
        (f"jweter/{product}",product,{"repository":f"jweter/{product}","exact_head":product,
         "slice_id":f"{product}-task","allowed_paths":[f"{product}/"]})
    ])
    from project_orchestrator.runner_registry import RunnerManifest,RunnerRegistry
    runners=RunnerRegistry([
        RunnerManifest("aider-ollama",frozenset({"code","local","windows","offline"}),1,30),
        RunnerManifest("claude-code",frozenset({"code","review","local","windows","offline"}),3,20),
    ])
    workers=supervisor.dispatch_parallel_builders(
        pool,tmp_path,tmp_path,tmp_path,["knowledge-engine","rocksmith","everward","voicestudio"],runners)
    assert workers==[
        "knowledge-engine-worker-1","rocksmith-worker-1","everward-worker-1",
        "voicestudio-worker-1"]
    snapshot=pool.snapshot()
    assert snapshot["running_by_product"]=={
        "knowledge-engine":1,"rocksmith":1,"everward":1,"voicestudio":1,"iron-god":0}


def test_parallel_dispatch_records_runner_block_for_convergence(tmp_path, monkeypatch):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    from project_orchestrator.parallel_worker_pool import ParallelWorkerPool
    from project_orchestrator.runner_registry import RunnerRegistry
    from tests.test_parallel_worker_pool import ManualExecutor
    pool=ParallelWorkerPool(max_workers_per_product=1,max_total_workers=1,executor_factory=ManualExecutor)
    monkeypatch.setattr(supervisor,"RUNTIME_LEASES",
                        supervisor.RuntimeLeasePool(supervisor.DEFAULT_RUNTIME_CAPACITIES))
    monkeypatch.setattr(supervisor,"ready_task_batch",lambda *args:[
        ("jweter/rocksmith-cdlc-generator","head",{"repository":"jweter/rocksmith-cdlc-generator",
         "exact_head":"head","slice_id":"slice","runner_requirements":["impossible"]})])
    assert supervisor.dispatch_parallel_builders(
        pool,tmp_path,tmp_path,tmp_path,["rocksmith"],RunnerRegistry([]),
        dispatch_invocation_id="current")==[]
    blocks=json.loads((tmp_path/"dispatch-blocks.json").read_text())
    assert blocks["invocation_id"]=="current"
    assert blocks["blocked"][0]["reason"]=="NO_ELIGIBLE_RUNNER"
    convergence=supervisor.product_convergence(
        tmp_path,["rocksmith"],current_invocation_id="current")
    assert convergence["status"]=="CONVERGED"
    assert convergence["explained_nonexecuting_products"]["rocksmith"]["reason"]=="NO_ELIGIBLE_RUNNER"


def test_parallel_worker_failure_is_recorded_without_blocking_pool(tmp_path):
    import json
    import scripts.continuous_flow_supervisor as supervisor
    from project_orchestrator.parallel_worker_pool import ParallelWorkerPool, WorkerSlot
    from tests.test_parallel_worker_pool import ManualExecutor
    executor=ManualExecutor(max_workers=2,thread_name_prefix="x")
    pool=ParallelWorkerPool(max_workers_per_product=1,max_total_workers=2,
                            executor_factory=lambda **_: executor)
    pool.submit(WorkerSlot("rocksmith",1),lambda:None)
    executor.submissions[0][0].set_exception(RuntimeError("private detail"))
    rows=supervisor.harvest_parallel_builders(pool,tmp_path)
    assert rows["rocksmith-worker-1"]["status"]=="WORKER_EXCEPTION"
    assert rows["rocksmith-worker-1"]["error_class"]=="RuntimeError"
    assert "private detail" not in json.dumps(rows)


def test_worker_liveness_treats_missing_process_as_dead_before_fingerprint(monkeypatch):
    import socket
    import scripts.continuous_flow_supervisor as supervisor
    import scripts.autonomous_slice_builder as builder
    import project_orchestrator.recovery_manager as recovery
    monkeypatch.setattr(recovery.sys, "platform", "linux")
    monkeypatch.setattr(supervisor.os,"kill",lambda pid,sig: (_ for _ in ()).throw(ProcessLookupError()))
    monkeypatch.setattr(builder,"process_start_fingerprint",lambda pid:None)
    worker={"host":socket.gethostname(),"pid":77,"process_start_fingerprint":"windows:old"}
    assert supervisor.worker_liveness(worker)=="DEAD"


def test_supervisor_status_requires_explicit_degradation_reason():
    from pathlib import Path
    source = Path("scripts/continuous_flow_supervisor.py").read_text(encoding="utf-8")
    assert 'healthy=not degraded_reasons' in source
    assert 'builder_status!="NO_READY_EXECUTION_SLICE"' in source
    assert '{"reason":"BUILDER_FAILED","status":builder_status,"returncode":rc}' in source

def test_resume_pushed_orphan_reconciles_pr_created_despite_failed_ack(tmp_path, monkeypatch):
    import scripts.continuous_flow_supervisor as supervisor
    calls=[]
    class Result:
        def __init__(self,returncode=0,stdout="",stderr=""):
            self.returncode=returncode; self.stdout=stdout; self.stderr=stderr
    def run(cmd,**kwargs):
        calls.append(cmd)
        if cmd[:2]==["git","rev-parse"]: return Result(stdout="head\n")
        if cmd[:2]==["git","ls-remote"]: return Result(stdout="head\trefs/heads/actor/autonomous-x\n")
        if cmd[:3]==["gh","pr","create"]: return Result(returncode=1,stderr="transport acknowledgement lost")
        if cmd[:3]==["gh","pr","list"]:
            return Result(stdout='[{"number":42,"url":"https://github/pr/42","headRefOid":"head"}]')
        raise AssertionError(cmd)
    monkeypatch.setattr(supervisor.subprocess,"run",run)
    assert supervisor.resume_orphan_branch(
        "jweter/rock",tmp_path,"actor/autonomous-x","ORPHAN_PUSHED_NO_PR",
        {"head_sha":"head","remote_head_sha":"head"})=="RECOVERY_PR_OPENED"
    assert any(cmd[:3]==["gh","pr","list"] for cmd in calls)


def test_resume_pushed_orphan_does_not_accept_existing_pr_at_wrong_head(tmp_path, monkeypatch):
    import scripts.continuous_flow_supervisor as supervisor
    class Result:
        def __init__(self,returncode=0,stdout="",stderr=""):
            self.returncode=returncode; self.stdout=stdout; self.stderr=stderr
    def run(cmd,**kwargs):
        if cmd[:2]==["git","rev-parse"]: return Result(stdout="head\n")
        if cmd[:2]==["git","ls-remote"]: return Result(stdout="head\trefs/heads/actor/autonomous-x\n")
        if cmd[:3]==["gh","pr","create"]: return Result(returncode=1)
        if cmd[:3]==["gh","pr","list"]:
            return Result(stdout='[{"number":42,"url":"https://github/pr/42","headRefOid":"different"}]')
        raise AssertionError(cmd)
    monkeypatch.setattr(supervisor.subprocess,"run",run)
    assert supervisor.resume_orphan_branch(
        "jweter/rock",tmp_path,"actor/autonomous-x","ORPHAN_PUSHED_NO_PR",
        {"head_sha":"head","remote_head_sha":"head"})=="RECOVERY_PR_CREATE_FAILED"

def test_actor_revision_timeout_fails_closed(tmp_path,monkeypatch):
    import subprocess
    def hung(argv,**kwargs):
        assert argv == ["git","rev-parse","HEAD"]
        assert kwargs["timeout"] == 10
        raise subprocess.TimeoutExpired(argv,10)
    monkeypatch.setattr(supervisor.subprocess,"run",hung)
    assert supervisor.actor_revision(tmp_path) is None


def test_actor_revision_rejects_failed_git_exit(tmp_path,monkeypatch):
    import subprocess
    def failed(argv,**kwargs):
        assert kwargs["check"] is True
        raise subprocess.CalledProcessError(128,argv,output="stale")
    monkeypatch.setattr(supervisor.subprocess,"run",failed)
    assert supervisor.actor_revision(tmp_path) is None
def test_live_head_probe_timeout_fails_closed(tmp_path,monkeypatch):
    import subprocess
    from project_orchestrator.activation_capability import observe_live_heads
    repo_root=tmp_path/"actor"
    workspace_root=tmp_path/"workspaces"
    state_root=tmp_path/"state"
    (repo_root/"engineering").mkdir(parents=True)
    (repo_root/"engineering"/"hands-off-evolution.json").write_text('{"products":{"test":["jweter/example"]}}')
    (workspace_root/"example").mkdir(parents=True)
    def timeout(argv,**kwargs):
        assert kwargs["timeout"] == 10
        raise subprocess.TimeoutExpired(argv,10)
    ok,reason=observe_live_heads(repo_root,workspace_root,state_root,run_fn=timeout)
    assert (ok,reason)==(False,"LIVE_HEADS_UNAVAILABLE")
    assert (state_root/"hands-off-live-heads.json").read_text().strip()=="{}"


def test_supervisor_source_identity_binding_blocks_before_slice_loading(tmp_path, monkeypatch):
    import json
    import pytest
    import scripts.continuous_flow_supervisor as supervisor

    state = tmp_path / "state"
    state.mkdir()
    activation = {"products": {"iron-god": {"repositories": {
        "jweter/project-orchestrator": "a" * 40
    }}}}
    observed = []
    monkeypatch.setattr(
        supervisor, "verify_activation_for_dispatch",
        lambda *args: "ACTIVATION_COMMIT_NOT_LOCAL",
    )
    monkeypatch.setattr(
        supervisor, "load_execution_slice",
        lambda *args: observed.append(args),
    )
    with pytest.raises(RuntimeError, match="ACTIVATION_COMMIT_NOT_LOCAL"):
        supervisor.select_execution_target(state, activation, ["iron-god"])
    assert observed == []
    decision = json.loads((state / "dispatch-decision.json").read_text())
    assert decision["decisions"][0]["outcome"] == "SOURCE_IDENTITY_BLOCKED"
