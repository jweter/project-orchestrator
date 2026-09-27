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


from project_orchestrator.parallel_runtime import scarce_capacity_plan


def _candidate(slice_id, runtime_requirements):
    return (
        "jweter/repo",
        "a" * 40,
        {
            "slice_id": slice_id,
            "runtime_requirements": runtime_requirements,
        },
    )


def test_saturated_unreal_defers_behind_runnable_sibling():
    batch = [
        _candidate("unreal", ["unreal"]),
        _candidate("native", []),
    ]
    snapshot = {
        "capacities": {"resource:unreal": 1, "resource:ollama": 1},
        "running": {"resource:unreal": 1, "resource:ollama": 0},
    }

    ordered, evidence = scarce_capacity_plan(batch, snapshot)

    assert [item[2]["slice_id"] for item in ordered] == ["native", "unreal"]
    by_slice = {row["slice_id"]: row for row in evidence}
    assert by_slice["unreal"]["queue_state"] == "LOCAL_RESOURCE_WAIT"
    assert by_slice["unreal"]["blocked_resources"] == ["resource:unreal"]
    assert by_slice["native"]["queue_state"] == "RUNNABLE"


def test_free_scarce_capacity_is_reserved_once_in_planning_order():
    batch = [
        _candidate("ollama-a", ["ollama"]),
        _candidate("ollama-b", ["ollama"]),
        _candidate("native", []),
    ]
    snapshot = {
        "capacities": {"resource:ollama": 1},
        "running": {"resource:ollama": 0},
    }

    ordered, evidence = scarce_capacity_plan(batch, snapshot)

    assert [item[2]["slice_id"] for item in ordered] == [
        "ollama-a",
        "native",
        "ollama-b",
    ]
    by_slice = {row["slice_id"]: row for row in evidence}
    assert by_slice["ollama-a"]["queue_state"] == "RUNNABLE"
    assert by_slice["ollama-b"]["queue_state"] == "LOCAL_RESOURCE_WAIT"


def test_free_unreal_capacity_preserves_existing_priority():
    batch = [
        _candidate("native-first", []),
        _candidate("unreal-second", ["unreal"]),
    ]
    snapshot = {
        "capacities": {"resource:unreal": 1},
        "running": {"resource:unreal": 0},
    }

    ordered, _ = scarce_capacity_plan(batch, snapshot)

    assert [item[2]["slice_id"] for item in ordered] == [
        "native-first",
        "unreal-second",
    ]
