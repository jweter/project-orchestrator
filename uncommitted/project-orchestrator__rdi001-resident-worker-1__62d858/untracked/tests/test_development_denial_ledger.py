"""Offline tests for denial history and independent work selection."""
import pytest

from project_orchestrator.development_denial_ledger import DenialLedger
from project_orchestrator.development_execution_policy import (
    DevelopmentTask,
    select_independent_task,
)


def test_roundtrip_preserves_denied_operation():
    original = DenialLedger(()).record("bridge-write", "SAFETY_DENIED")
    restored = DenialLedger.parse(original.as_dict())
    assert restored == original
    assert select_independent_task(
        [
            DevelopmentTask("bridge-again", "bridge-write", priority=100),
            DevelopmentTask("other", "independent-task"),
        ],
        denied=restored.denials,
    ).task_id == "other"


def test_repeat_denial_never_clears_or_rewrites_evidence():
    original = DenialLedger(()).record("bridge-write", "SAFETY_DENIED")
    assert original.record("bridge-write", "retry") is original


@pytest.mark.parametrize("payload", [
    {},
    {"schema_version": 2, "denials": []},
    {"schema_version": 1, "denials": None},
    {"schema_version": 1, "denials": [{"operation_id": "", "reason": "blocked"}]},
    {"schema_version": 1, "denials": [
        {"operation_id": "x", "reason": "blocked"},
        {"operation_id": "x", "reason": "blocked"},
    ]},
])
def test_malformed_history_fails_closed(payload):
    with pytest.raises(ValueError):
        DenialLedger.parse(payload)


def test_empty_history_roundtrip():
    assert DenialLedger.parse(DenialLedger(()).as_dict()) == DenialLedger(())
