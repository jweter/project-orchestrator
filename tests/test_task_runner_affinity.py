import json
from project_orchestrator.runner_registry import RunnerManifest,RunnerRegistry
import scripts.continuous_flow_supervisor as supervisor

def registry():
    return RunnerRegistry([
        RunnerManifest("claude-code",frozenset({"code","local","windows","offline"}),2,20),
        RunnerManifest("aider-ollama",frozenset({"code","local","windows","offline"}),2,30),
    ])

def write(path,grants):
    (path/"hands-off-grant-lifecycle.json").write_text(json.dumps({"grants":grants}),encoding="utf-8")

def test_task_affinity_class_prefers_explicit():
    assert supervisor.task_affinity_class({"task_class":"Unreal"})=="unreal"

def test_task_affinity_class_derives_repair_from_objective():
    assert supervisor.task_affinity_class({"objective":"Fix parser failure","allowed_paths":["src/"]})=="repair"

def test_affinity_can_outweigh_global_history_after_evidence(tmp_path):
    grants={}
    for i in range(4):
        grants[f"cg{i}"]={"state":"COMPLETED","runner_id":"claude-code","result_status":"GREEN",
            "execution_slice":{"objective":"general feature","task_class":"code"}}
        grants[f"af{i}"]={"state":"ABANDONED","runner_id":"aider-ollama","result_status":"BUILDER_FAILED",
            "execution_slice":{"objective":"general feature","task_class":"code"}}
        grants[f"cf{i}"]={"state":"ABANDONED","runner_id":"claude-code","result_status":"BUILDER_FAILED",
            "execution_slice":{"objective":"fix parser bug","task_class":"repair"}}
        grants[f"ag{i}"]={"state":"COMPLETED","runner_id":"aider-ollama","result_status":"GREEN",
            "execution_slice":{"objective":"fix parser bug","task_class":"repair"}}
    write(tmp_path,grants)
    assert supervisor.select_adaptive_runner({"task_class":"repair"},tmp_path,registry(),{})=="aider-ollama"

def test_affinity_history_is_class_scoped(tmp_path):
    write(tmp_path,{
      "a":{"state":"COMPLETED","runner_id":"claude-code","result_status":"GREEN","execution_slice":{"task_class":"tests"}},
      "b":{"state":"COMPLETED","runner_id":"aider-ollama","result_status":"GREEN","execution_slice":{"task_class":"repair"}},
    })
    assert supervisor.runner_affinity_history(tmp_path,"tests")=={"claude-code":{"success":1,"failure":0}}
