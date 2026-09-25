import pytest
import scripts.continuous_flow_supervisor as supervisor
from project_orchestrator.execution_runtime import (
    LaptopActorRuntime,ExecutionRuntimeRegistry,require_runtime_capabilities
)

def test_laptop_actor_attests_only_detected_optional_capabilities(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil,"which",lambda name:"C:/bin/ollama.exe" if name=="ollama" else None)
    caps=LaptopActorRuntime().attest()
    assert {"windows","local","offline","ollama"} <= caps
    assert "unreal" not in caps

def test_runtime_requirement_fails_closed_when_not_attested(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil,"which",lambda name:None)
    reg=ExecutionRuntimeRegistry([LaptopActorRuntime()])
    with pytest.raises(RuntimeError,match="EXECUTION_RUNTIME_CAPABILITY_MISMATCH"):
        require_runtime_capabilities("laptop-actor",["unreal"],reg)

def test_runtime_requirement_passes_when_attested(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil,"which",lambda name:"C:/bin/ollama.exe" if name=="ollama" else None)
    reg=ExecutionRuntimeRegistry([LaptopActorRuntime()])
    assert require_runtime_capabilities("laptop-actor",["ollama"],reg).runtime_id=="laptop-actor"

def test_scheduler_does_not_assume_unreal_from_windows(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil,"which",lambda name:None)
    with pytest.raises(RuntimeError,match="CAPABILITY_MISMATCH"):
        supervisor.runtime_for_task({"runtime_requirements":["unreal"]})
