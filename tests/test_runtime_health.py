from project_orchestrator.execution_runtime import (
    LaptopActorRuntime,ExecutionRuntimeRegistry,runtime_health
)

def registry():
    return ExecutionRuntimeRegistry([LaptopActorRuntime()])

def test_runtime_health_is_available_below_capacity():
    h=runtime_health("laptop-actor",0,2,registry())
    assert h.status=="HEALTHY" and h.available

def test_runtime_health_reports_saturation():
    h=runtime_health("laptop-actor",2,2,registry())
    assert h.status=="SATURATED" and not h.available

def test_runtime_health_fails_closed_for_missing_runtime():
    h=runtime_health("missing",0,1,registry())
    assert h.status=="UNAVAILABLE" and not h.available

def test_runtime_health_fails_closed_when_attestation_errors(monkeypatch):
    monkeypatch.setattr(LaptopActorRuntime,"attest",lambda self:(_ for _ in ()).throw(RuntimeError("probe")))
    h=runtime_health("laptop-actor",0,1,registry())
    assert h.status=="UNAVAILABLE" and not h.available
