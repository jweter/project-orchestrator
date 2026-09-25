from project_orchestrator.runtime_leases import RuntimeLeasePool,DEFAULT_RUNTIME_CAPACITIES
import scripts.continuous_flow_supervisor as supervisor

def test_ollama_and_unreal_are_independent_scarce_resources():
    assert DEFAULT_RUNTIME_CAPACITIES["laptop-actor"]>1
    assert DEFAULT_RUNTIME_CAPACITIES["resource:ollama"]==1
    assert DEFAULT_RUNTIME_CAPACITIES["resource:unreal"]==1
    assert supervisor.resource_leases_for_task({"runtime_requirements":["ollama"]},"laptop-actor")==("resource:ollama",)
    assert supervisor.resource_leases_for_task({"runtime_requirements":["unreal"]},"laptop-actor")==("resource:unreal",)

def test_laptop_workers_can_run_concurrently_while_ollama_stays_singleton():
    pool=RuntimeLeasePool(DEFAULT_RUNTIME_CAPACITIES)
    pool.acquire("laptop-actor","w1")
    pool.acquire("laptop-actor","w2")
    pool.acquire("resource:ollama","w1:resource:ollama")
    try:
        pool.acquire("resource:ollama","w2:resource:ollama")
        assert False,"second Ollama lease must be rejected"
    except RuntimeError as exc:
        assert "RUNTIME_SATURATED" in str(exc)
    pool.acquire("resource:unreal","w2:resource:unreal")
