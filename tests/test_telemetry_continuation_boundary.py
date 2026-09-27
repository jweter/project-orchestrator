import inspect

import project_orchestrator.telemetry_continuation as telemetry
import scripts.continuous_flow_supervisor as supervisor


def test_supervisor_telemetry_and_continuation_are_thin_adapters():
    for name in (
        "planner_decision_quality",
        "reconcile_dashboard_ci",
        "write_dashboard_snapshot",
        "record_async_gate_rotation",
        "record_burst_repository_pass",
        "record_product_identity_advancement",
        "selected_builder_repository",
        "maybe_record_async_rotation",
        "continuation_delay",
        "reconcile_continuation_journal",
        "record_continuation_pass",
    ):
        source = inspect.getsource(getattr(supervisor, name))
        assert "_telemetry." in source


def test_telemetry_module_owns_dashboard_and_continuation_evidence():
    source = inspect.getsource(telemetry)
    assert "foundry-dashboard.json" in source
    assert "continuation-pass-history.json" in source
    assert "continuation-pass-journal.json" in source
    assert "async-gate-rotation-history.json" in source
    assert "portfolio-product-advancement-proof.json" in source


def test_dashboard_ci_adapter_preserves_observational_run_injection():
    source = inspect.getsource(supervisor.reconcile_dashboard_ci)
    assert "subprocess.run" in source


def test_supervisor_no_longer_owns_hash_chain_implementation():
    source = inspect.getsource(supervisor)
    assert 'def record_continuation_pass(state_root: Path' not in source
    assert 'def write_dashboard_snapshot(state_root: Path' not in source
