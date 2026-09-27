import inspect

import project_orchestrator.workspace_recovery as workspace_recovery
import scripts.continuous_flow_supervisor as supervisor


def test_supervisor_delegates_workspace_recovery_implementation():
    source = inspect.getsource(supervisor)
    assert "def preserved_work_evidence(" not in source
    assert "def reconcile_abandoned_claims(" not in source
    assert "def recheck_preserved_work_quarantines(" in source
    adapter = inspect.getsource(supervisor.recheck_preserved_work_quarantines)
    assert "_workspace_recovery.recheck_preserved_work_quarantines" in adapter


def test_workspace_recovery_module_owns_preservation_and_orphan_logic():
    source = inspect.getsource(workspace_recovery)
    assert "PRESERVED_COMMIT_RECOVERED" in source
    assert "CONSUMED_RECOVERY_EXACT_STAGED" in source
    assert "RECOVERY_DURABLE_PR_HANDOFF" in source
    assert "UNCOMMITTED_WORK_REQUIRES_PRESERVATION" in source


def test_public_supervisor_adapter_preserves_validation_injection():
    adapter = inspect.getsource(supervisor.validate_preserved_work)
    assert "revalidate_preserved_work" in adapter
    adapter = inspect.getsource(supervisor.commit_preserved_work)
    assert "consume_preserved_recovery_capability" in adapter
    adapter = inspect.getsource(supervisor.begin_recovery_generation)
    assert "worker_liveness" in adapter
