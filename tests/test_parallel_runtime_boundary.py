import inspect

import project_orchestrator.parallel_runtime as runtime
import scripts.continuous_flow_supervisor as supervisor


def test_supervisor_runtime_helpers_are_thin_adapters():
    for name in (
        "runner_requirements_for_task",
        "select_task_runner",
        "runtime_for_task",
        "resource_leases_for_task",
        "select_adaptive_runner",
        "durable_runner_occupancy",
        "dispatch_parallel_builders",
    ):
        source = inspect.getsource(getattr(supervisor, name))
        assert "_parallel_runtime." in source


def test_parallel_runtime_module_owns_capacity_and_dispatch_policy():
    source = inspect.getsource(runtime)
    assert "RUNNER_ADAPTER_UNAVAILABLE" in source
    assert "RUNTIME_LEASE_UNAVAILABLE" in source
    assert "NO_ELIGIBLE_RUNNER" in source
    assert "NO_CONFLICT_FREE_READY_SLICE" in source


def test_durable_occupancy_adapter_preserves_liveness_injection():
    source = inspect.getsource(supervisor.durable_runner_occupancy)
    assert "worker_liveness" in source


def test_runtime_adapter_preserves_capability_attestation_injection():
    source = inspect.getsource(supervisor.runtime_for_task)
    assert "require_runtime_capabilities" in source
