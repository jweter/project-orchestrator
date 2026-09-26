import json
from project_orchestrator.runner_registry import RunnerManifest,RunnerRegistry
import scripts.continuous_flow_supervisor as supervisor

def registry():
    return RunnerRegistry([
        RunnerManifest("claude-code",frozenset({"code","local","windows","offline"}),2,20),
        RunnerManifest("aider-ollama",frozenset({"code","local","windows","offline"}),1,30),
    ])

def write_history(path,grants):
    (path/"hands-off-grant-lifecycle.json").write_text(json.dumps({"grants":grants}),encoding="utf-8")

def test_adaptive_runner_uses_static_priority_without_history(tmp_path):
    assert supervisor.select_adaptive_runner({},tmp_path,registry(),{})=="claude-code"

def test_adaptive_runner_prefers_verified_reliability(tmp_path):
    write_history(tmp_path,{
      "c1":{"state":"ABANDONED","runner_id":"claude-code","result_status":"BUILDER_FAILED"},
      "c2":{"state":"ABANDONED","runner_id":"claude-code","result_status":"PREFLIGHT_FAILED"},
      "a1":{"state":"COMPLETED","runner_id":"aider-ollama","result_status":"PR_OPEN"},
      "a2":{"state":"COMPLETED","runner_id":"aider-ollama","result_status":"GREEN"},
    })
    assert supervisor.select_adaptive_runner({},tmp_path,registry(),{})=="aider-ollama"

def test_adaptive_runner_accounts_for_current_utilization(tmp_path):
    assert supervisor.select_adaptive_runner({},tmp_path,registry(),{"claude-code":1})=="aider-ollama"

def test_adaptive_runner_routes_around_recent_capacity_exhaustion(tmp_path):
    from datetime import datetime,UTC
    write_history(tmp_path,{
      "c1":{"state":"DEFERRED","runner_id":"claude-code","result_status":"RUNNER_CAPACITY_DEFERRED",
            "finished_at":datetime.now(UTC).isoformat()},
    })
    assert supervisor.runner_capacity_cooldowns(tmp_path)=={"claude-code"}
    assert supervisor.select_adaptive_runner({},tmp_path,registry(),{})=="aider-ollama"

def test_history_ignores_nonterminal_claims(tmp_path):
    write_history(tmp_path,{
      "x":{"state":"CLAIMED","runner_id":"aider-ollama","result_status":"BUILDER_FAILED"},
      "y":{"state":"COMPLETED","runner_id":"claude-code","result_status":"GREEN"},
    })
    assert supervisor.runner_verified_history(tmp_path)=={"claude-code":{"success":1,"failure":0}}
