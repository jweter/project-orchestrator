"""Offline durable denial tests using only pytest-owned temporary directories."""
import json

import pytest

from project_orchestrator.development_denial_ledger import DenialLedger
from project_orchestrator.development_denial_store import load_denials, persist_denials
from project_orchestrator.development_execution_policy import (
    DevelopmentTask,
    select_independent_task,
)


def test_missing_history_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="MISSING_OR_CORRUPT"):
        load_denials(tmp_path / "history.json")


def test_initialization_requires_explicit_permission(tmp_path):
    path = tmp_path / "history.json"
    with pytest.raises(ValueError, match="INITIALIZATION_NOT_AUTHORIZED"):
        persist_denials(path, DenialLedger(()))
    assert not path.exists()


def test_persist_restart_and_select_independent_work(tmp_path):
    path = tmp_path / "history.json"
    initial = DenialLedger(()).record("write-bridge", "SAFETY_DENIED")
    persist_denials(path, initial, allow_initialize=True)
    restarted = load_denials(path)
    chosen = select_independent_task(
        [DevelopmentTask("retry", "write-bridge", priority=10),
         DevelopmentTask("unrelated", "test-new-module")],
        denied=restarted.denials,
    )
    assert chosen.task_id == "unrelated"
    updated = restarted.record("launch-controller", "NOT_AUTHORIZED")
    persist_denials(path, updated)
    assert load_denials(path) == updated


def test_cannot_erase_or_relabel_denial(tmp_path):
    path = tmp_path / "history.json"
    initial = DenialLedger(()).record("write-bridge", "SAFETY_DENIED")
    persist_denials(path, initial, allow_initialize=True)
    for attempted in (DenialLedger(()),
                      DenialLedger(()).record("write-bridge", "different")):
        with pytest.raises(ValueError, match="ROLLBACK_FORBIDDEN"):
            persist_denials(path, attempted)
    assert load_denials(path) == initial


def test_corrupt_snapshot_never_reinitialized(tmp_path):
    path = tmp_path / "history.json"
    path.write_text("{invalid", encoding="utf-8")
    with pytest.raises(ValueError):
        persist_denials(path, DenialLedger(()), allow_initialize=True)
    assert path.read_text(encoding="utf-8") == "{invalid"


def test_symlink_snapshot_is_rejected(tmp_path):
    target = tmp_path / "target.json"
    target.write_text(json.dumps(DenialLedger(()).as_dict()), encoding="utf-8")
    link = tmp_path / "history.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink privilege unavailable")
    with pytest.raises(ValueError, match="PATH_UNTRUSTED"):
        load_denials(link)
    with pytest.raises(ValueError, match="PATH_UNTRUSTED"):
        persist_denials(link, DenialLedger(()))


def test_duplicate_initialization_is_monotonic(tmp_path):
    path = tmp_path / "history.json"
    original = DenialLedger(()).record("denied", "SAFETY_DENIED")
    persist_denials(path, original, allow_initialize=True)
    with pytest.raises(ValueError, match="ROLLBACK_FORBIDDEN"):
        persist_denials(path, DenialLedger(()), allow_initialize=True)
