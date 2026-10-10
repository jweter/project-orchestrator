import inspect

import project_orchestrator.supervisor_runtime as runtime
import scripts.continuous_flow_supervisor as supervisor


def test_supervisor_runtime_support_is_delegated():
    for name in (
        "builder_health",
        "refresh_decision_learning",
        "acquire_supervisor_instance_lock",
        "release_supervisor_instance_lock",
        "write_execution_grant",
        "mark_builder_running",
        "run_builder",
        "run_builder_for_product",
        "harvest_parallel_builders",
        "prove_restart_takeover",
        "start_or_resume_burst",
    ):
        source = inspect.getsource(getattr(supervisor, name))
        assert "_supervisor_runtime." in source


def test_runtime_support_module_owns_launch_and_restart_semantics():
    source = inspect.getsource(runtime)
    assert "PLANNED_SLICE_IDENTITY_MISMATCH" in source
    assert "EXECUTION_GRANT_IDENTITY_REQUIRED" in source
    assert "supervisor-instance.lock" in source
    assert "RESTART_PROVEN_BY_EXCLUSIVE_KERNEL_LOCK" in source


def test_supervisor_no_longer_owns_private_os_lock_implementation():
    source = inspect.getsource(supervisor)
    assert "def _acquire_os_file_lock(" not in source
    assert "def _release_os_file_lock(" not in source


def test_lifecycle_recovery_pass_defers_permission_contention(tmp_path):
    def busy(_state):
        raise PermissionError("destination busy")

    result = runtime.run_lifecycle_recovery_pass(
        tmp_path, tmp_path,
        recover_generations_fn=busy,
        recover_claims_fn=lambda _state: [],
        pending_fn=lambda _state, _abandoned: [],
        preserved_fn=lambda _workspace, _state: {},
        quarantined_fn=lambda _state: {},
    )

    assert result["publication_blocked"] is True
    assert result["released"] == []
    assert result["quarantine_rechecks"] == {}


def test_lifecycle_recovery_pass_terminalizes_safe_preclaim_quarantines(tmp_path):
    from project_orchestrator.supervisor_runtime import run_lifecycle_recovery_pass

    seen = []
    result = run_lifecycle_recovery_pass(
        tmp_path, tmp_path,
        recover_generations_fn=lambda _state: [],
        recover_claims_fn=lambda _state: [],
        pending_fn=lambda _state, _abandoned: [],
        preserved_fn=lambda _workspace, _state: {},
        quarantined_fn=lambda _state: {},
        terminalize_fn=lambda state: seen.append(state) or {"g": "TERMINALIZED_PRECLAIM_NO_PRODUCT_WORK"},
    )
    assert seen == [tmp_path]
    assert result["terminalized_quarantines"] == {
        "g": "TERMINALIZED_PRECLAIM_NO_PRODUCT_WORK"
    }
