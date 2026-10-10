"""IG-014/015/016 shadow health, durability and JSON compatibility tests."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from project_orchestrator.local_state import LocalStateStore, OwnerControlBlocked
from project_orchestrator.local_state_recovery import (
    export_snapshot,
    health_check,
    online_backup,
)


@pytest.fixture
def sample(tmp_path: Path) -> tuple[LocalStateStore, Path]:
    owner = tmp_path / "human-mode.json"
    owner.write_text('{"enabled": false}', encoding="utf-8")
    store = LocalStateStore(tmp_path / "state.sqlite", owner_state_path=owner)
    store.initialize()
    store.append_event("e1", "SLICE_PLANNED", "slice-1", {"sha": "abcd"})
    return store, owner


def test_health_is_read_only_and_reports_lease_evidence(sample):
    store, owner = sample
    lease = store.claim_lease("voice", "worker-1", "fingerprint")
    result = health_check(store)
    assert result == {
        "status": "HEALTHY", "reasons": [], "schema_version": 1,
        "event_count": 2, "lease_count": 1,
    }
    owner.write_text('{"enabled": true}', encoding="utf-8")
    assert health_check(store) == result  # inspection is permitted in human mode
    owner.write_text('{"enabled": false}', encoding="utf-8")
    assert store.release_lease("voice", lease, "fingerprint")


def test_missing_or_unreadable_database_never_returns_healthy(sample):
    store, _ = sample
    missing = LocalStateStore(
        store.database.parent / "does-not-exist.sqlite",
        owner_state_path=store.owner_state_path,
    )
    assert health_check(missing)["status"] == "UNHEALTHY"
    assert not missing.database.exists()
    with sqlite3.connect(store.database) as db:
        db.execute("DROP TRIGGER events_immutable_update")
    result = health_check(store)
    assert result["status"] == "UNHEALTHY"
    assert "EVENT_IMMUTABILITY_TRIGGERS_MISSING" in result["reasons"]


def test_backup_is_consistent_and_does_not_change_source(sample, tmp_path):
    store, _ = sample
    store.claim_lease("everward", "w1", "p123")
    original = store.snapshot()
    target = tmp_path / "checkpoint.sqlite"
    result = online_backup(store, target)
    assert result["status"] == "BACKED_UP"
    assert result["event_count"] == 2
    assert result["lease_count"] == 1
    checkpoint = LocalStateStore(target, owner_state_path=store.owner_state_path)
    assert checkpoint.snapshot() == original
    assert checkpoint.integrity_check()
    assert store.snapshot() == original
    assert not list(tmp_path.glob(".checkpoint.sqlite.pending-*"))


def test_backup_restores_verified_content_after_source_removed(sample, tmp_path):
    store, _ = sample
    target = tmp_path / "safe.sqlite"
    online_backup(store, target)
    store.database.unlink()
    recovered = LocalStateStore(target, owner_state_path=store.owner_state_path)
    assert recovered.integrity_check()
    assert recovered.snapshot()["events"][0]["kind"] == "SLICE_PLANNED"


def test_backup_never_overwrites_and_does_not_delete_unrelated_data(sample, tmp_path):
    store, _ = sample
    target = tmp_path / "existing.sqlite"
    target.write_bytes(b"valuable owner data")
    with pytest.raises(FileExistsError):
        online_backup(store, target)
    assert target.read_bytes() == b"valuable owner data"
    assert not list(tmp_path.glob(".existing.sqlite.pending-*"))


def test_take_the_wheel_vetoes_backup_and_export(sample, tmp_path):
    store, owner = sample
    owner.write_text('{"enabled": true}', encoding="utf-8")
    with pytest.raises(OwnerControlBlocked):
        online_backup(store, tmp_path / "no-backup.sqlite")
    with pytest.raises(OwnerControlBlocked):
        export_snapshot(store, tmp_path / "no-export.json")
    assert not list(tmp_path.glob("no-*"))
    owner.write_text("invalid", encoding="utf-8")
    with pytest.raises(OwnerControlBlocked):
        online_backup(store, tmp_path / "no-backup.sqlite")


def test_deterministic_json_export_and_reopen(sample, tmp_path):
    store, _ = sample
    store.claim_lease("knowledge-engine", "w1", "fp")
    first = tmp_path / "snapshot-a.json"
    second = tmp_path / "snapshot-b.json"
    result = export_snapshot(store, first)
    assert result["status"] == "EXPORTED"
    assert result["events"] == 2
    export_snapshot(store, second)
    assert first.read_bytes() == second.read_bytes()
    assert json.loads(first.read_text(encoding="utf-8")) == store.snapshot()


def test_json_export_does_not_overwrite_existing_data(sample, tmp_path):
    store, _ = sample
    target = tmp_path / "snapshot.json"
    target.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        export_snapshot(store, target)
    assert target.read_text(encoding="utf-8") == "keep"


def test_parent_not_present_fails_without_creating_files(sample, tmp_path):
    store, _ = sample
    invalid = tmp_path / "missing" / "backup.sqlite"
    with pytest.raises(FileNotFoundError):
        online_backup(store, invalid)
    with pytest.raises(FileNotFoundError):
        export_snapshot(store, invalid.with_suffix(".json"))
    assert not invalid.parent.exists()


def test_health_and_backup_handle_uri_special_database_paths(tmp_path):
    import os

    owner = tmp_path / "human-mode.json"
    owner.write_text('{"enabled": false}', encoding="utf-8")
    name = "events#local?resume.sqlite" if os.name != "nt" else "events#local%20.sqlite"
    store = LocalStateStore(tmp_path / name, owner_state_path=owner)
    store.initialize()
    store.append_event("s1", "SLICE_PLANNED", "s1", {"repository": "voice"})
    assert health_check(store)["status"] == "HEALTHY"
    target = tmp_path / "archive#test.sqlite"
    assert online_backup(store, target)["status"] == "BACKED_UP"
    assert LocalStateStore(target, owner_state_path=owner).snapshot() == store.snapshot()
