import pytest
from project_orchestrator.runner_registry import RunnerManifest, RunnerRegistry

def registry():
    return RunnerRegistry([
        RunnerManifest("local",frozenset({"code","windows","offline"}),1,30),
        RunnerManifest("fast",frozenset({"code","review"}),2,10),
        RunnerManifest("deep",frozenset({"code","review","architecture"}),1,20),
        RunnerManifest("disabled",frozenset({"code","review","architecture"}),5,1,False),
    ])

def test_selects_highest_priority_explicitly_capable_runner():
    assert registry().select({"code","review"}).runner_id=="fast"

def test_capability_requirements_route_specialized_task():
    assert registry().select({"code","architecture"}).runner_id=="deep"

def test_capacity_exhaustion_routes_to_next_capable_runner():
    assert registry().select({"code","review"},{"fast":2}).runner_id=="deep"

def test_disabled_runner_is_never_selected():
    assert registry().select({"code","architecture"}).runner_id=="deep"

def test_no_capable_runner_fails_closed():
    with pytest.raises(RuntimeError,match="NO_ELIGIBLE_RUNNER"):
        registry().select({"unreal"})

def test_empty_requirements_fail_closed():
    with pytest.raises(ValueError,match="requirements"):
        registry().select(set())

def test_duplicate_runner_ids_rejected():
    m=RunnerManifest("x",frozenset({"code"}))
    with pytest.raises(ValueError,match="duplicate"):
        RunnerRegistry([m,m])

def test_invalid_manifest_rejected():
    with pytest.raises(ValueError):
        RunnerManifest("",frozenset({"code"}))
    with pytest.raises(ValueError):
        RunnerManifest("x",frozenset())
    with pytest.raises(ValueError):
        RunnerManifest("x",frozenset({"code"}),0)

import scripts.continuous_flow_supervisor as supervisor

def test_legacy_task_routes_to_proven_local_runner():
    assert supervisor.select_task_runner({"slice_id":"S"})=="aider-ollama"

def test_task_can_explicitly_request_capabilities():
    r=RunnerRegistry([RunnerManifest("special",frozenset({"code","unreal"}))])
    assert supervisor.select_task_runner({"runner_requirements":["code","unreal"]},r)=="special"

def test_invalid_task_requirements_fail_closed():
    with pytest.raises(RuntimeError,match="INVALID_RUNNER_REQUIREMENTS"):
        supervisor.runner_requirements_for_task({"runner_requirements":[]})


def test_default_registry_does_not_authorize_claude_from_authentication_alone(monkeypatch):
    import project_orchestrator.runner_registry as runners
    monkeypatch.delenv("AZATHOTH_ENABLE_CLAUDE", raising=False)
    monkeypatch.setattr(runners, "claude_code_healthy", lambda: True)
    selected = runners.default_runner_registry().select({"code","local","windows","offline"})
    assert selected.runner_id == "aider-ollama"


def test_default_registry_requires_explicit_claude_opt_in(monkeypatch):
    import project_orchestrator.runner_registry as runners
    monkeypatch.setenv("AZATHOTH_ENABLE_CLAUDE", "1")
    monkeypatch.setattr(runners, "claude_code_healthy", lambda: True)
    selected = runners.default_runner_registry().select({"code","local","windows","offline"})
    assert selected.runner_id == "claude-code"
