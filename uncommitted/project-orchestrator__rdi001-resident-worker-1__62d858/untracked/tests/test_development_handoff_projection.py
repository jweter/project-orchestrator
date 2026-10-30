"""No controller or worker execution: offline handoff admission."""
import pytest

from project_orchestrator.development_denial_ledger import DenialLedger
from project_orchestrator.development_denial_store import persist_denials
from project_orchestrator.development_handoff_projection import project_handoffs, select_handoff


def test_denied_handoff_rotates_to_independent_slice(tmp_path):
    history = tmp_path / "denials.json"
    persist_denials(history, DenialLedger(()).record("write-bridge", "SAFETY_DENIED"), allow_initialize=True)
    handoffs = [
        {"id": "RDI-001-retry", "operation_id": "write-bridge", "priority": 100},
        {"id": "IG-073-audit", "operation_id": "independent-audit", "priority": 5},
    ]
    assert select_handoff(handoffs, history_path=history).task_id == "IG-073-audit"


def test_unknown_operation_identity_fails_closed():
    with pytest.raises(ValueError, match="OPERATION_ID_UNVERIFIED"):
        project_handoffs([{"id": "IG-001"}])


def test_invalid_dependencies_fail_closed():
    with pytest.raises(ValueError, match="METADATA_UNVERIFIED"):
        project_handoffs([{"id": "a", "operation_id": "op-a", "dependencies": "anything"}])


def test_missing_denial_history_never_dispatches(tmp_path):
    with pytest.raises(ValueError, match="MISSING_OR_CORRUPT"):
        select_handoff([{"id": "a", "operation_id": "op-a"}], history_path=tmp_path / "absent")


def test_dependency_fence_preserved(tmp_path):
    history = tmp_path / "denials.json"
    persist_denials(history, DenialLedger(()), allow_initialize=True)
    handoffs = [{"id": "b", "operation_id": "op-b", "dependencies": ["a"]}]
    assert select_handoff(handoffs, history_path=history) is None
    assert select_handoff(handoffs, history_path=history, completed=frozenset({"a"})).task_id == "b"
