from scripts.adaptive_flow_controller import ResourceSnapshot,launch_decision,adaptive_delay

def make(cpu=20,mem=8000):
    return ResourceSnapshot(cpu,mem,16000)

def test_capacity_when_resources_available():
    result=launch_decision(make())
    assert result["launch_heavy_builder"] is True
    assert result["heavy_builder_capacity"]==1
    assert result["verification_capacity"]==2

def test_memory_backpressure():
    result=launch_decision(make(mem=2500))
    assert result["launch_heavy_builder"] is False
    assert "LOW_AVAILABLE_MEMORY" in result["reasons"]

def test_cpu_backpressure():
    assert launch_decision(make(cpu=95))["launch_heavy_builder"] is False

def test_immediate_refill_after_lifecycle_progress():
    result=launch_decision(make())
    assert adaptive_delay(decision=result,builder_status="PR_OPEN",lifecycle_advanced=True,normal_seconds=300)==1

def test_pressure_recheck_is_bounded():
    result=launch_decision(make(mem=2500))
    assert adaptive_delay(decision=result,builder_status="RESOURCE_BACKPRESSURE",lifecycle_advanced=False,normal_seconds=300)==30
