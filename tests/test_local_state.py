"""IG-010 through IG-013 shadow-state safety and durability contracts."""
from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from project_orchestrator.local_state import (
    LeaseConflict,
    LocalStateStore,
    OwnerControlBlocked,
    StateConflict,
)


@pytest.fixture
def subject(tmp_path: Path) -> tuple[LocalStateStore, Path]:
    owner = tmp_path / "flow-state" / "human-mode.json"
    owner.parent.mkdir()
    owner.write_text('{"enabled": false}', encoding="utf-8")
    store = LocalStateStore(tmp_path / "db" / "iron-god.sqlite3", owner_state_path=owner)
    return store, owner


def test_owner_authority_blocks_missing_malformed_or_take_the_wheel(subject):
    store, owner = subject
    owner.unlink()
    with pytest.raises(OwnerControlBlocked, match="UNVERIFIED"):
        store.initialize()
    assert not store.database.exists()
    for payload in ("not json", "{}", '{"enabled": 0}', '{"enabled": true}'):
        owner.write_text(payload, encoding="utf-8")
        with pytest.raises(OwnerControlBlocked):
            store.initialize()
        assert not store.database.exists()


def test_schema_is_repeatable_wal_full_and_no_destructive_migration(subject):
    store, _ = subject
    store.initialize()
    store.initialize()
    assert store.integrity_check()
    with sqlite3.connect(store.database) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 1
        assert set(row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )) >= {"schema_migrations", "events", "leases"}
    other = LocalStateStore(store.database, owner_state_path=store.owner_state_path)
    assert other.snapshot() == {"schema_version": 1, "events": [], "leases": []}


def test_replay_is_idempotent_ordered_and_conflicts_fail_closed(subject):
    store, _ = subject
    store.initialize()
    sequence = store.append_event("m1", "SLICE_PLANNED", "slice-1", {"b": 2, "a": 1})
    assert sequence == 1
    assert store.append_event("m1", "SLICE_PLANNED", "slice-1", {"a": 1, "b": 2}) == 1
    assert store.append_event("m2", "LOCAL_COMMIT_CREATED", "slice-1", {"sha": "abc"}) == 2
    with pytest.raises(StateConflict, match="conflicting event"):
        store.append_event("m1", "MILESTONE_ACCEPTED", "slice-1", {})
    assert [event["sequence"] for event in store.snapshot()["events"]] == [1, 2]


def test_event_journal_is_immutable_even_by_direct_sql(subject):
    store, _ = subject
    store.initialize()
    store.append_event("e1", "SLICE_PLANNED", "s1", {})
    with sqlite3.connect(store.database) as db:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("UPDATE events SET kind='FAKE' WHERE sequence=1")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("DELETE FROM events WHERE sequence=1")
    assert store.snapshot()["events"][0]["kind"] == "SLICE_PLANNED"


def test_atomic_lease_and_journal_survive_reopen(subject):
    store, _ = subject
    store.initialize()
    token = store.claim_lease("rocksmith", "worker-1", "pid-123")
    assert store.heartbeat("rocksmith", token, "wrong") is False
    assert store.heartbeat("rocksmith", token, "pid-123") is True
    with pytest.raises(LeaseConflict):
        store.claim_lease("rocksmith", "worker-2", "pid-456")
    assert not store.release_lease("rocksmith", token, "pid-456")
    assert store.release_lease("rocksmith", token, "pid-123")
    assert not store.release_lease("rocksmith", token, "pid-123")
    with pytest.raises(LeaseConflict, match="previously used"):
        store.claim_lease("rocksmith", "worker-1", "pid-123", lease_id=token)
    reopened = LocalStateStore(store.database, owner_state_path=store.owner_state_path)
    snapshot = reopened.snapshot()
    assert snapshot["leases"] == []
    assert [event["kind"] for event in snapshot["events"]] == [
        "WORKER_CLAIMED", "WORKER_RELEASED"
    ]
    assert reopened.integrity_check()


def test_failed_journal_transition_rolls_back_lease(subject):
    store, _ = subject
    store.initialize()
    # A colliding journal id must never leave a lease with no claiming event.
    # Simulate an existing legacy/corrupt reservation written outside the public API.
    with sqlite3.connect(store.database) as db:
        db.execute(
            "INSERT INTO events(event_id,kind,subject,evidence_json,recorded_at) VALUES(?,?,?,?,?)",
            ("lease-claimed:collision", "SOME_OTHER_EVENT", "x", "{}", "legacy"),
        )
    with pytest.raises(LeaseConflict):
        store.claim_lease("voice", "worker-1", "fingerprint", lease_id="collision")
    assert store.snapshot()["leases"] == []


def test_concurrent_claims_have_one_winner_and_no_duplicate_claim_events(subject):
    store, _ = subject
    store.initialize()

    def attempt(number: int) -> str | None:
        try:
            return store.claim_lease("knowledge-engine", f"worker-{number}", f"pid-{number}")
        except LeaseConflict:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(attempt, range(16)))
    assert sum(bool(token) for token in outcomes) == 1
    assert len(store.snapshot()["leases"]) == 1
    assert [e["kind"] for e in store.snapshot()["events"]] == ["WORKER_CLAIMED"]


def test_owner_control_switch_blocks_subsequent_mutation(subject):
    store, owner = subject
    store.initialize()
    store.append_event("e1", "SLICE_PLANNED", "s1", {})
    owner.write_text(json.dumps({"enabled": True}), encoding="utf-8")
    with pytest.raises(OwnerControlBlocked, match="ACTIVE"):
        store.append_event("e2", "WORKER_STARTED", "s1", {})
    with pytest.raises(OwnerControlBlocked):
        store.claim_lease("everward", "w1", "fp")
    assert len(store.snapshot()["events"]) == 1
    assert store.snapshot()["leases"] == []


def test_reading_absent_database_does_not_create_it(subject):
    store, _ = subject
    with pytest.raises(FileNotFoundError):
        store.snapshot()
    assert not store.integrity_check()
    assert not store.database.exists()


@pytest.mark.parametrize("forged_prefix", ["lease-released:", "lease-claimed:"])
def test_user_event_cannot_reserve_internal_lease_ids(subject, forged_prefix):
    store, _ = subject
    store.initialize()
    lease_id = store.claim_lease("rocksmith", "worker", "fp")
    with pytest.raises(ValueError, match="reserved"):
        store.append_event(forged_prefix + lease_id, "FORGED", "rocksmith", {})
    assert store.release_lease("rocksmith", lease_id, "fp")
    assert store.snapshot()["leases"] == []
    assert [event["kind"] for event in store.snapshot()["events"]] == [
        "WORKER_CLAIMED", "WORKER_RELEASED"
    ]


def test_read_only_uri_escapes_uri_special_characters(tmp_path):
    import os

    owner = tmp_path / "human-mode.json"
    owner.write_text('{"enabled": false}', encoding="utf-8")
    filename = "state#question?token.sqlite" if os.name != "nt" else "state#fragment%20.sqlite"
    store = LocalStateStore(tmp_path / filename, owner_state_path=owner)
    store.initialize()
    store.append_event("safe", "SLICE_PLANNED", "s1", {"path": filename})
    assert store.integrity_check()
    assert store.snapshot()["events"][0]["event_id"] == "safe"
