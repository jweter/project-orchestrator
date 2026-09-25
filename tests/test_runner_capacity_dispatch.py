from project_orchestrator.runner_registry import RunnerManifest,RunnerRegistry
import scripts.continuous_flow_supervisor as supervisor

def test_runner_capacity_spills_work_to_second_lane():
    registry=RunnerRegistry([
        RunnerManifest("claude-code",frozenset({"code"}),1,10),
        RunnerManifest("aider-ollama",frozenset({"code"}),1,20),
    ])
    running={}
    first=supervisor.select_task_runner({"runner_requirements":["code"]},registry,running)
    running[first]=running.get(first,0)+1
    second=supervisor.select_task_runner({"runner_requirements":["code"]},registry,running)
    assert (first,second)==("claude-code","aider-ollama")

def test_runner_capacity_fails_when_all_lanes_saturated():
    import pytest
    registry=RunnerRegistry([
        RunnerManifest("claude-code",frozenset({"code"}),1,10),
        RunnerManifest("aider-ollama",frozenset({"code"}),1,20),
    ])
    with pytest.raises(RuntimeError,match="NO_ELIGIBLE_RUNNER"):
        supervisor.select_task_runner({"runner_requirements":["code"]},registry,{"claude-code":1,"aider-ollama":1})
