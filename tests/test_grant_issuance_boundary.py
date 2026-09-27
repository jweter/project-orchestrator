import inspect

import project_orchestrator.grant_issuance as issuance
import scripts.continuous_flow_supervisor as supervisor


def test_supervisor_delegates_grant_finalization_to_shared_module():
    assert supervisor.finalize_unclaimed_grant is issuance.finalize_unclaimed_grant
    assert supervisor.release_grant_issue_lock is issuance.release_grant_issue_lock


def test_supervisor_conflict_check_is_thin_adapter():
    source = inspect.getsource(supervisor.conflicting_nonterminal_grant)
    assert "_conflicting_nonterminal_grant" in source
    assert "hands-off-grant-lifecycle.json" not in source


def test_grant_issuance_module_owns_durable_reservation_and_finalization():
    source = inspect.getsource(issuance)
    assert "hands-off-execution-grants" in source
    assert "hands-off-grant-lifecycle.json" in source
    assert "BUILDER_EXITED_WITHOUT_CLAIM" in source


def test_supervisor_no_longer_defines_grant_finalizer():
    source = inspect.getsource(supervisor)
    assert "def finalize_unclaimed_grant(" not in source
    assert "def release_grant_issue_lock(" not in source
