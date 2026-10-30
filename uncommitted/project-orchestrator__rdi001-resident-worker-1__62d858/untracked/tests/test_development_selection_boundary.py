"""Offline selection boundary tests: never run a real worker."""
import pytest

from project_orchestrator.development_denial_ledger import DenialLedger
from project_orchestrator.development_denial_store import persist_denials
from project_orchestrator.development_execution_policy import DevelopmentTask
from project_orchestrator.development_selection_boundary import select_from_verified_history


def test_durable_denial_excludes_retried_operation(tmp_path):
    history = tmp_path / "denials.json"
    persist_denials(history, DenialLedger(()).record("blocked-operation", "SAFETY_DENIED"), allow_initialize=True)
    tasks = [
        DevelopmentTask("renamed-retry", "blocked-operation", priority=100),
        DevelopmentTask("independent", "new-operation"),
    ]
    assert select_from_verified_history(tasks, history_path=history).task_id == "independent"


def test_missing_history_blocks_all_selection(tmp_path):
    with pytest.raises(ValueError, match="MISSING_OR_CORRUPT"):
        select_from_verified_history(
            [DevelopmentTask("independent", "new-operation")],
            history_path=tmp_path / "missing.json",
        )


def test_corrupt_history_blocks_all_selection(tmp_path):
    history = tmp_path / "denials.json"
    history.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="MISSING_OR_CORRUPT"):
        select_from_verified_history(
            [DevelopmentTask("independent", "new-operation")], history_path=history
        )


def test_dependency_fence_preserved(tmp_path):
    history = tmp_path / "denials.json"
    persist_denials(history, DenialLedger(()), allow_initialize=True)
    tasks = [DevelopmentTask("dependent", "safe-op", dependencies=("first",))]
    assert select_from_verified_history(tasks, history_path=history) is None
    assert select_from_verified_history(
        tasks, history_path=history, completed=frozenset({"first"})
    ).task_id == "dependent"
