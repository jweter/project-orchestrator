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

def test_recover_stale_grant_claims_marks_expired_claim_abandoned(tmp_path):
    import json
    from datetime import UTC, datetime, timedelta
    import scripts.continuous_flow_supervisor as supervisor
    state=tmp_path
    now=datetime(2026,9,19,23,0,tzinfo=UTC)
    old=(now-timedelta(seconds=supervisor.CLAIM_LEASE_SECONDS+1)).isoformat()
    (state/"hands-off-grant-lifecycle.json").write_text(json.dumps({
        "schema_version":1,"grants":{"g1":{"state":"CLAIMED","claimed_at":old}}}),encoding="utf-8")
    assert supervisor.recover_stale_grant_claims(state,now)==["g1"]
    record=json.loads((state/"hands-off-grant-lifecycle.json").read_text())["grants"]["g1"]
    assert record["state"]=="ABANDONED"
    assert record["result_status"]=="CLAIM_LEASE_EXPIRED"
    assert record["finished_at"]==now.isoformat()

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
    assert supervisor.reconcile_abandoned_claims(wsroot,state,["g1"])=={"g1":"ORPHAN_BRANCH_QUARANTINED"}

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
