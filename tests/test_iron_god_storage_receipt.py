"""Disk accounting is read-only, bounded and never treated as cleanup proof."""
from __future__ import annotations

import json
from types import SimpleNamespace

from project_orchestrator import iron_god_storage_receipt as storage


def test_storage_receipt_observes_owned_counts_and_steward_pressure(tmp_path, monkeypatch):
    state = tmp_path / "flow-state"
    archive = state / "iron-god-products" / "reports" / "worktree-archive"
    archive.mkdir(parents=True)
    (archive / "P1.json").write_text("recoverable", encoding="utf-8")
    worktrees = tmp_path / "canon-workspaces" / "products" / "worktrees"
    (worktrees / "P1").mkdir(parents=True)
    marker = worktrees / "P1" / "do-not-delete.txt"
    marker.write_text("unfinished", encoding="utf-8")
    gib = 2 ** 30
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _: SimpleNamespace(
        free=35 * gib, total=500 * gib,
    ))

    result = storage.record_storage_receipt(state, {"removed": 1, "eligible": 2})

    assert result["mode"] == "READ_ONLY_ACCOUNTING_NO_DELETION"
    assert result["disk"]["free_bytes"] == 35 * gib
    assert result["disk"]["band"] == "ORANGE"
    assert result["observed_free_delta_bytes"] is None
    assert result["counts"]["product_worktrees"]["count"] == 1
    assert result["counts"]["recovery_archive_files"]["count"] == 1
    assert result["last_cleanup"]["removed"] == 1
    assert marker.read_text(encoding="utf-8") == "unfinished"
    path = state / "iron-god-products" / "reports" / storage.RECEIPT_FILENAME
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1


def test_storage_receipt_bounded_history_and_observational_delta(tmp_path, monkeypatch):
    state = tmp_path / "flow-state"
    state.mkdir()
    gib = 2 ** 30
    free = [50 * gib]
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _: SimpleNamespace(
        free=free[0], total=500 * gib,
    ))
    assert storage.record_storage_receipt(state, None)["observed_free_delta_bytes"] is None
    free[0] = 45 * gib
    second = storage.record_storage_receipt(state, {"removed": 2})
    assert second["observed_free_delta_bytes"] == -5 * gib
    assert second["delta_is_not_cleanup_attribution"] is True
    assert len(second["samples"]) == 2
    for _ in range(storage.MAX_SAMPLES + 3):
        storage.record_storage_receipt(state, None)
    final = storage.record_storage_receipt(state, None)
    assert len(final["samples"]) == storage.MAX_SAMPLES
    assert final["last_cleanup"] == {"status": "NOT_DUE"}


def test_storage_receipt_marks_missing_telemetry_unknown_not_green(tmp_path, monkeypatch):
    state = tmp_path / "flow-state"
    state.mkdir()

    def unavailable(_):
        raise OSError("disk sensor missing")

    monkeypatch.setattr(storage.shutil, "disk_usage", unavailable)
    result = storage.record_storage_receipt(state, None)
    assert result["disk"]["status"] == "UNAVAILABLE"
    assert result["disk"]["band"] == "UNKNOWN"
    assert result["disk"]["free_bytes"] is None
    assert result["observed_free_delta_bytes"] is None
    assert result["counts"]["product_worktrees"]["status"] == "ABSENT"


def test_storage_receipt_does_not_follow_links(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "sensitive.txt").write_text("preserved", encoding="utf-8")
    link = tmp_path / "linked"
    try:
        link.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        return  # Symlink creation can require elevated privileges on Windows.
    assert storage._count_owned_entries(link, directories=True) == {
        "status": "LINK_PROTECTED", "count": None,
    }
    assert (real / "sensitive.txt").read_text(encoding="utf-8") == "preserved"
