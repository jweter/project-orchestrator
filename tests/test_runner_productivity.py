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
    assert row=={"verified_advancement":2.0,"seconds":180.0,"failures":0.0,"samples":2.0}

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

def test_productivity_penalty_requires_multiple_samples():
    stats={"x":{"verified_advancement":1.0,"seconds":1.0,"failures":0.0,"samples":1.0}}
    assert supervisor.productivity_penalty("x",stats)==0.0
