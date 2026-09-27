import inspect

import project_orchestrator.planning_dispatch as planning
import scripts.continuous_flow_supervisor as supervisor


def test_supervisor_selection_and_liveness_are_thin_adapters():
    for name in (
        "select_execution_target",
        "ready_task_batch",
        "productive_liveness",
        "product_convergence",
        "request_liveness_reacquire",
    ):
        source = inspect.getsource(getattr(supervisor, name))
        assert "_planning." in source


def test_planning_module_owns_selection_and_idle_defect_semantics():
    source = inspect.getsource(planning)
    assert "ALL_EXECUTION_LANES_COMPLETE" in source
    assert "NO_CONFLICT_FREE_READY_SLICE" in source
    assert "AUTHORIZED_NO_SLICE" in source
    assert "P1_LIVENESS_DEFECT" in source


def test_supervisor_no_longer_owns_planner_algorithms():
    source = inspect.getsource(supervisor)
    assert 'def task_outcome_priority(state_root: Path' not in source
    assert 'def runner_productivity_history(state_root: Path' not in source
    assert 'def slice_paths_conflict(left: dict' not in source


def test_product_repository_mapping_has_single_owner():
    assert supervisor.PRODUCT_BY_REPOSITORY is planning.PRODUCT_BY_REPOSITORY
    assert supervisor.product_for_repository("jweter/knowledge-engine-ai") == "knowledge-engine"
