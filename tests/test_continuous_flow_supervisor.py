from scripts.continuous_flow_supervisor import atomic
def test_atomic_supervisor_state(tmp_path):
    path=tmp_path/"state.json"; atomic(path,{"status":"HEALTHY"})
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert '"status": "HEALTHY"' in path.read_text(encoding="utf-8")


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
    from scripts.continuous_flow_supervisor import merge_watcher_advanced
    (tmp_path/"green-pr-watcher.json").write_text(
        json.dumps({"results":[{"repository":"r","pr":1,"merged":True}]}),encoding="utf-8")
    assert merge_watcher_advanced(tmp_path) is True

def test_merge_watcher_does_not_advance_on_promotion_only(tmp_path):
    import json
    from scripts.continuous_flow_supervisor import merge_watcher_advanced
    (tmp_path/"green-pr-watcher.json").write_text(
        json.dumps({"results":[{"repository":"r","pr":1,"promoted":True}]}),encoding="utf-8")
    assert merge_watcher_advanced(tmp_path) is False

def test_merge_event_is_consumed_exactly_once(tmp_path):
    import json
    from scripts.continuous_flow_supervisor import merge_watcher_advanced
    payload={"updated_at_utc":"2026-09-19T22:00:00+00:00",
             "results":[{"repository":"r","pr":9,"head":"abc","merged":True}]}
    (tmp_path/"green-pr-watcher.json").write_text(json.dumps(payload),encoding="utf-8")
    assert merge_watcher_advanced(tmp_path) is True
    assert merge_watcher_advanced(tmp_path) is False

def test_new_merge_event_advances_after_previous_consumed(tmp_path):
    import json
    from scripts.continuous_flow_supervisor import merge_watcher_advanced
    path=tmp_path/"green-pr-watcher.json"
    path.write_text(json.dumps({"updated_at_utc":"t1","results":[{"repository":"r","pr":1,"head":"a","merged":True}]}),encoding="utf-8")
    assert merge_watcher_advanced(tmp_path) is True
    path.write_text(json.dumps({"updated_at_utc":"t2","results":[{"repository":"r","pr":2,"head":"b","merged":True}]}),encoding="utf-8")
    assert merge_watcher_advanced(tmp_path) is True

def test_refresh_readiness_fails_closed_for_incomplete_workspace(tmp_path):
    from scripts.continuous_flow_supervisor import refresh_readiness
    ok,reason=refresh_readiness(tmp_path,tmp_path/"workspace",tmp_path/"state")
    assert ok is False
    assert reason=="READINESS_WORKSPACE_INCOMPLETE"

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
        assert cmd[cmd.index("--product")+1]=="rocksmith"
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
        if cmd[:3]==["git","rev-parse","HEAD"]:
            return Result("live-sha\n")
        raise AssertionError(cmd)
    monkeypatch.setattr(supervisor.subprocess,"run",run)
    assert supervisor.observe_live_heads(repo_root,workspace,state)==(True,"LIVE_HEADS_OBSERVED")
    assert json.loads((state/"hands-off-live-heads.json").read_text())=={
        "jweter/rocksmith-cdlc-generator":"live-sha"}

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
    calls=[]
    class Result:
        returncode=1; stdout=""; stderr=""
    def run(cmd,**kwargs):
        calls.append(cmd)
        # Simulate auditor writing a readable partial authority.
        partial=state/"hands-off-recertify-rocksmith.json"
        partial.write_text(json.dumps({"products":{"rocksmith":{"enabled":False,"repositories":{}}}}))
        return Result()
    monkeypatch.setattr(supervisor.subprocess,"run",run)
    monkeypatch.setattr(supervisor,"merge_product_recertification",lambda *a:(False,"PRODUCT_QUARANTINED"))
    supervisor.recertify_product(repo_root,workspace,state,"rocksmith")
    cmd=calls[0]
    assert str(workspace/"rocksmith-cdlc-generator") in cmd
    assert str(workspace/"knowledge-engine-core") not in cmd
    assert str(workspace/"knowledge-engine-web") not in cmd

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
    assert supervisor.run_builder(tmp_path,tmp_path/"workspace",state,None)==2
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
    monkeypatch.setattr(supervisor.os,"kill",lambda pid,sig: None)
    monkeypatch.setattr(builder,"process_start_fingerprint",lambda pid:"linux:boot:new")
    worker={"host":socket.gethostname(),"pid":77,"process_start_fingerprint":"linux:boot:old"}
    assert supervisor.worker_liveness(worker)=="DEAD"

def test_worker_liveness_accepts_matching_process_fingerprint(monkeypatch):
    import socket
    import scripts.continuous_flow_supervisor as supervisor
    import scripts.autonomous_slice_builder as builder
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

def test_stage_aware_recovery_hands_off_verified_pr_open(tmp_path,monkeypatch):
    import json
    from datetime import UTC, datetime
    import scripts.continuous_flow_supervisor as supervisor
    wsroot=tmp_path/"ws"; ws=wsroot/"r"; ws.mkdir(parents=True); state=tmp_path/"state"; state.mkdir()
    path=state/"hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants":{"g":{"state":"ABANDONED","repository":"o/r","execution_stage":"PR_OPEN","branch":"actor/autonomous-r-x","head_sha":"abc","recovery_attempts":0}}}))
    class R:
        returncode=0; stdout="abc refs/heads/actor/autonomous-r-x\n"; stderr=""
    monkeypatch.setattr(supervisor.subprocess,"run",lambda *a,**k:R())
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
    record={"preserved_work_evidence":{"diff_sha256":"abc"}}
    checks=iter(["PRESERVED_WORK_EXACT","PRESERVED_WORK_EXACT"])
    monkeypatch.setattr(supervisor,"revalidate_preserved_work",lambda ws,r:next(checks))
    class R: returncode=0; stdout=""; stderr=""
    monkeypatch.setattr(supervisor.subprocess,"run",lambda *a,**k:R())
    assert supervisor.validate_preserved_work(tmp_path,record)=="PRESERVED_VALIDATION_PASSED"

def test_validate_preserved_work_detects_validator_mutation(tmp_path,monkeypatch):
    import scripts.continuous_flow_supervisor as supervisor
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

def test_recovery_capability_is_exact_bound_and_single_use(tmp_path,monkeypatch):
    import scripts.continuous_flow_supervisor as supervisor
    record={"repository":"o/r","base_sha":"base","branch":"actor/x","preserved_work_validation":"PRESERVED_VALIDATION_PASSED",
            "preserved_work_evidence":{"diff_sha256":"diff","head_sha":"base"}}
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
            "preserved_work_evidence":{"diff_sha256":"diff","head_sha":"base"}}
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
