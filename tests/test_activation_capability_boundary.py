import inspect

import project_orchestrator.activation_capability as authority
import scripts.continuous_flow_supervisor as supervisor


def test_supervisor_authority_helpers_are_thin_adapters():
    for name in (
        "observe_live_heads",
        "evolution_authorized",
        "refresh_readiness",
        "recertify_product",
        "merge_product_recertification",
        "request_immediate_builder",
        "reconcile_merge_feedback",
        "merge_watcher_advanced",
        "reconcile_verified_merge",
        "reconcile_capability_from_slice_evidence",
        "reconcile_product_completion",
        "repeated_noop_slice_evidence",
        "record_exhausted_slice_direction",
        "load_execution_slice",
        "direction_request_outbox",
        "publish_direction_outbox",
    ):
        source = inspect.getsource(getattr(supervisor, name))
        assert "_authority." in source


def test_authority_module_owns_capability_and_merge_feedback_semantics():
    source = inspect.getsource(authority)
    assert "MERGE_SLICE_IDENTITY_UNPROVEN" in source
    assert "CAPABILITY_COMPLETION_CONFLICT" in source
    assert "REPEATED_NO_OP_SLICE_EXHAUSTED" in source
    assert "PRODUCT_COMPLETION_VERIFIED" in source
    assert "DIRECTION_REQUIRED" in source


def test_supervisor_preserves_mutable_test_seams():
    assert "subprocess.run" in inspect.getsource(supervisor.observe_live_heads)
    assert "merge_product_recertification" in inspect.getsource(supervisor.recertify_product)
    assert "reconcile_verified_merge" in inspect.getsource(supervisor.reconcile_merge_feedback)
    assert "reconcile_capability_from_slice_evidence" in inspect.getsource(supervisor.reconcile_merge_feedback)
    assert "derive_slice" in inspect.getsource(supervisor.load_execution_slice)


def test_noop_exhaustion_constants_have_single_owner():
    assert supervisor.NO_OP_EXHAUSTION_THRESHOLD == authority.NO_OP_EXHAUSTION_THRESHOLD
    assert supervisor.NO_OP_TERMINAL_STATUSES == authority.NO_OP_TERMINAL_STATUSES
