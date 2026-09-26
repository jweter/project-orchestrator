import json
from datetime import UTC,datetime,timedelta
from project_orchestrator.runner_registry import RunnerManifest,RunnerRegistry
import scripts.continuous_flow_supervisor as supervisor

def terminal(runner,status,seconds,task_class="repair",state="COMPLETED"):
    start=datetime(2026,9,25,20,0,tzinfo=UTC)
    return {"state":state,"runner_id":runner,"result_status":status,
      "issued_at":start.isoformat(),"claimed_at":start.isoformat(),
      "finished_at":(start+timedelta(seconds=seconds)).isoformat(),
      "execution_slice":{"task_class":task_class}}

def test_productivity_history_measures_verified_advancement_per_time(tmp_path):
    payload={"grants":{"a":terminal("claude-code","GREEN",60),"b":terminal("claude-code","GREEN",120)}}
    (tmp_path/"hands-off-grant-lifecycle.json").write_text(json.dumps(payload),encoding="utf-8")
    row=supervisor.runner_productivity_history(tmp_path,"repair")["claude-code"]
    assert row=={"verified_advancement":2.0,"seconds":180.0,"failures":0.0,"samples":2.0,"repair_attempts":0.0,"merged_verified":0.0,"product_reality_verified":0.0}

def test_productivity_ignores_records_without_duration(tmp_path):
    payload={"grants":{"a":{"state":"COMPLETED","runner_id":"claude-code","result_status":"GREEN","execution_slice":{"task_class":"repair"}}}}
    (tmp_path/"hands-off-grant-lifecycle.json").write_text(json.dumps(payload),encoding="utf-8")
    assert supervisor.runner_productivity_history(tmp_path,"repair")=={}

def test_productivity_can_prefer_faster_equally_reliable_runner(tmp_path):
    grants={}
    for i in range(2):
        grants[f"c{i}"]=terminal("claude-code","GREEN",600)
        grants[f"a{i}"]=terminal("aider-ollama","GREEN",60)
    (tmp_path/"hands-off-grant-lifecycle.json").write_text(json.dumps({"grants":grants}),encoding="utf-8")
    registry=RunnerRegistry([
      RunnerManifest("claude-code",frozenset({"code","local","windows","offline"}),2,20),
      RunnerManifest("aider-ollama",frozenset({"code","local","windows","offline"}),2,30)])
    assert supervisor.select_adaptive_runner({"task_class":"repair"},tmp_path,registry,{})=="aider-ollama"

def test_productivity_prefers_explicit_execution_seconds_over_queue_lifetime(tmp_path):
    item=terminal("claude-code","GREEN",600)
    item["execution_seconds"]=30
    payload={"grants":{"a":item}}
    (tmp_path/"hands-off-grant-lifecycle.json").write_text(json.dumps(payload),encoding="utf-8")
    assert supervisor.runner_productivity_history(tmp_path,"repair")["claude-code"]["seconds"]==30.0

def test_repair_amplification_increases_productivity_penalty():
    clean={"x":{"verified_advancement":2.0,"seconds":120.0,"failures":0.0,"samples":2.0,"repair_attempts":0.0}}
    repaired={"x":{"verified_advancement":2.0,"seconds":120.0,"failures":0.0,"samples":2.0,"repair_attempts":2.0}}
    assert supervisor.productivity_penalty("x",repaired)>supervisor.productivity_penalty("x",clean)

def test_verified_merge_quality_reduces_penalty():
    opened={"x":{"verified_advancement":2.0,"seconds":120.0,"failures":0.0,"samples":2.0,"repair_attempts":0.0,"merged_verified":0.0}}
    merged={"x":{"verified_advancement":2.0,"seconds":120.0,"failures":0.0,"samples":2.0,"repair_attempts":0.0,"merged_verified":2.0}}
    assert supervisor.productivity_penalty("x",merged)<supervisor.productivity_penalty("x",opened)

def test_product_reality_quality_outweighs_merge_only():
    merged={"x":{"verified_advancement":2.0,"seconds":120.0,"failures":0.0,"samples":2.0,"repair_attempts":0.0,"merged_verified":2.0,"product_reality_verified":0.0}}
    reality={"x":{"verified_advancement":2.0,"seconds":120.0,"failures":0.0,"samples":2.0,"repair_attempts":0.0,"merged_verified":2.0,"product_reality_verified":2.0}}
    assert supervisor.productivity_penalty("x",reality)<supervisor.productivity_penalty("x",merged)

def test_task_outcome_priority_prefers_product_reality_yield(tmp_path):
    strong=terminal("claude-code","GREEN",60,task_class="tests")
    strong["outcome_quality"]="PRODUCT_REALITY_VERIFIED"
    weak=terminal("claude-code","GREEN",60,task_class="documentation")
    payload={"grants":{"strong":strong,"weak":weak}}
    (tmp_path/"hands-off-grant-lifecycle.json").write_text(json.dumps(payload),encoding="utf-8")
    assert supervisor.task_outcome_priority(tmp_path,{"task_class":"tests"}) < supervisor.task_outcome_priority(tmp_path,{"task_class":"documentation"})

def test_task_outcome_priority_explores_unseen_over_known_zero_yield(tmp_path):
    known=terminal("claude-code","GREEN",60,task_class="documentation")
    payload={"grants":{"known":known}}
    (tmp_path/"hands-off-grant-lifecycle.json").write_text(json.dumps(payload),encoding="utf-8")
    assert supervisor.task_outcome_priority(tmp_path,{"task_class":"tests"}) < supervisor.task_outcome_priority(tmp_path,{"task_class":"documentation"})

def test_task_exploration_bonus_decays_after_four_samples(tmp_path):
    grants={f"k{i}":terminal("claude-code","GREEN",60,task_class="documentation") for i in range(4)}
    (tmp_path/"hands-off-grant-lifecycle.json").write_text(json.dumps({"grants":grants}),encoding="utf-8")
    score=supervisor.task_outcome_priority(tmp_path,{"task_class":"documentation"})
    assert score[0]==0.0
    assert score[1]==4

def test_planner_decision_quality_joins_verified_outcomes(tmp_path):
    merged=terminal("claude-code","GREEN",60,task_class="tests")
    merged.update({"planner_decision_id":"d1","outcome_quality":"MERGED_VERIFIED","repair_attempts":1})
    reality=terminal("claude-code","GREEN",60,task_class="tests")
    reality.update({"planner_decision_id":"d1","outcome_quality":"PRODUCT_REALITY_VERIFIED"})
    failed=terminal("claude-code","TEST_FAILURE",60,task_class="tests")
    failed.update({"planner_decision_id":"d2","state":"ABANDONED"})
    (tmp_path/"hands-off-grant-lifecycle.json").write_text(
        json.dumps({"grants":{"m":merged,"r":reality,"f":failed}}),encoding="utf-8")
    quality=supervisor.planner_decision_quality(tmp_path)
    assert quality["d1"]=={"samples":2.0,"failures":0.0,"repairs":1.0,
                           "merged_verified":2.0,"product_reality_verified":1.0}
    assert quality["d2"]["failures"]==1.0

def test_dashboard_snapshot_projects_active_work_and_planner_state(tmp_path):
    grant=terminal("claude-code","GREEN",60,task_class="tests")
    grant.update({"state":"CLAIMED","planner_decision_id":"d1","slice_id":"S1",
                  "execution_slice":{"repository":"jweter/repo","task_class":"tests"}})
    (tmp_path/"hands-off-grant-lifecycle.json").write_text(json.dumps({"grants":{"g1":grant}}),encoding="utf-8")
    decisions=tmp_path/"planner-decisions"; decisions.mkdir()
    (decisions/"rocksmith.json").write_text(json.dumps({"candidate_count":3,"selected_count":1,"candidates":[{"slice_id":"S1"}]}),encoding="utf-8")
    snapshot=supervisor.write_dashboard_snapshot(tmp_path)
    assert snapshot["active_count"]==1
    assert snapshot["active_work"][0]["repository"]=="jweter/repo"
    assert snapshot["queue"]["decisions"]==[]
    assert snapshot["products"]["rocksmith"]["candidate_count"]==3
    assert snapshot["outcome_counters"]=={"completed":0,"failed":0,"merged_verified":0,"product_reality_verified":0,"repairs":0}
    assert (tmp_path/"telemetry"/"foundry-dashboard.json").exists()

def test_dashboard_snapshot_projects_running_worker_pool(tmp_path):
    (tmp_path/"parallel-worker-pool.json").write_text(json.dumps({"pool":{"running_total":2,"workers":[
        {"worker_id":"rocksmith-worker-1","product":"rocksmith","slot":1,"done":False},
        {"worker_id":"everward-worker-1","product":"everward","slot":1,"done":False}]}}),encoding="utf-8")
    snapshot=supervisor.write_dashboard_snapshot(tmp_path)
    assert snapshot["active_count"]==2
    assert [w["worker_id"] for w in snapshot["active_worker_pool"]]==["rocksmith-worker-1","everward-worker-1"]

def test_dashboard_snapshot_writer_creates_telemetry_directory(tmp_path):
    snapshot=supervisor.write_dashboard_snapshot(tmp_path)
    assert snapshot["schema_version"]==1
    assert (tmp_path/"telemetry"/"foundry-dashboard.json").is_file()

def test_dashboard_ci_reconciliation_is_observational(tmp_path,monkeypatch):
    record={"state":"COMPLETED","repository":"jweter/repo","pr_number":42,"head_sha":"abc"}
    (tmp_path/"hands-off-grant-lifecycle.json").write_text(json.dumps({"grants":{"g1":record}}),encoding="utf-8")
    class Result:
        returncode=0
        stdout=json.dumps([{"name":"CI","state":"SUCCESS","link":"https://example.test/ci"}])
    monkeypatch.setattr(supervisor.subprocess,"run",lambda *args,**kwargs:Result())
    observed=supervisor.reconcile_dashboard_ci(tmp_path,tmp_path)
    assert observed["pull_requests"][0]["ci_conclusion"]=="SUCCESS"
    persisted=json.loads((tmp_path/"hands-off-grant-lifecycle.json").read_text(encoding="utf-8"))
    assert persisted["grants"]["g1"]==record

def test_dashboard_snapshot_projects_dispatch_block_reasons(tmp_path):
    dispatch={"selected_repository":"jweter/ready","selected_product":"rocksmith","decisions":[
        {"repository":"jweter/busy","outcome":"ALREADY_OWNED","reason":"NONTERMINAL_GRANT","grant_id":"g1"},
        {"repository":"jweter/waiting","outcome":"NOT_READY","reason":"DEPENDENCY_BLOCKED"},
        {"repository":"jweter/ready","outcome":"SELECTED","reason":"READY_EXECUTION_SLICE"}]}
    (tmp_path/"dispatch-decision.json").write_text(json.dumps(dispatch),encoding="utf-8")
    rotations={"rotations":[{"blocked_repository":"jweter/waiting","blocked_status":"CI_PENDING","next_repository":"jweter/ready","rotated":True}]}
    (tmp_path/"async-gate-rotation-history.json").write_text(json.dumps(rotations),encoding="utf-8")
    snapshot=supervisor.write_dashboard_snapshot(tmp_path)
    assert snapshot["queue"]["selected_repository"]=="jweter/ready"
    assert snapshot["queue"]["decisions"][0]["outcome"]=="ALREADY_OWNED"
    assert snapshot["queue"]["recent_async_rotations"][0]["blocked_status"]=="CI_PENDING"

def test_dashboard_snapshot_counts_and_orders_terminal_outcomes(tmp_path):
    older=terminal("aider-ollama","GREEN",60,task_class="tests")
    older.update({"finished_at":"2026-09-25T10:00:00+00:00","outcome_quality":"MERGED_VERIFIED","repair_attempts":2,"execution_seconds":60,
                  "pr_number":42,"pr_url":"https://github.com/jweter/repo/pull/42","head_sha":"abc","execution_stage":"CI_PENDING"})
    newer=terminal("claude-code","GREEN",60,task_class="tests")
    newer.update({"finished_at":"2026-09-25T11:00:00+00:00","outcome_quality":"PRODUCT_REALITY_VERIFIED","execution_seconds":60})
    failed=terminal("claude-code","TEST_FAILURE",60,task_class="tests")
    failed.update({"state":"ABANDONED","finished_at":"2026-09-25T12:00:00+00:00","execution_seconds":60})
    (tmp_path/"hands-off-grant-lifecycle.json").write_text(json.dumps({"grants":{"old":older,"new":newer,"bad":failed}}),encoding="utf-8")
    snapshot=supervisor.write_dashboard_snapshot(tmp_path)
    assert snapshot["outcome_counters"]=={"completed":2,"failed":1,"merged_verified":2,"product_reality_verified":1,"repairs":2}
    assert snapshot["flow"]["measured_worker_seconds"]==180.0
    assert snapshot["flow"]["verified_advancement_per_worker_hour"]==40.0
    assert snapshot["github"]["pull_requests"][0]["pr_number"]==42
    assert snapshot["github"]["promotion_gates"][0]=={"repository":None,"pr_number":42,"head_sha":"abc","stage":"CI_PENDING","result_status":"GREEN"}
    assert [row["grant_id"] for row in snapshot["recent_completed_work"]]==["bad","new","old"]

def test_productivity_penalty_requires_multiple_samples():
    stats={"x":{"verified_advancement":1.0,"seconds":1.0,"failures":0.0,"samples":1.0,"repair_attempts":0.0,"merged_verified":0.0,"product_reality_verified":0.0}}
    assert supervisor.productivity_penalty("x",stats)==0.0
