"""S1 state durability: transactional migrations, replay and multi-writer safety.

All tests use disposable SQLite files and isolated checkpoint directories. They
never open the resident Azathoth state, start a supervisor, or grant execution.
"""
from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from project_orchestrator import iron_god_development_memory as memory
from project_orchestrator import iron_god_microtask_progress as progress
from project_orchestrator.state_io import atomic_json

REPO = "jweter/knowledge-engine-web"
PARENT = "RM-web-s1-durable-progress"


def _attempt_packet(packet_id: str) -> dict:
    return {
        "packet_id": packet_id, "repository": REPO, "base_sha": "a" * 40,
        "slice": {"id": "RM-web-s1"},
    }


def _attempt_result() -> dict:
    return {
        "status": "FAIL", "summary": "SLICE_TESTS_FAILED",
        "plan": {"mode": "directed", "targets": ["f"]},
    }


def _rows(path) -> list[tuple]:
    with sqlite3.connect(path) as db:
        return db.execute(
            "SELECT packet_id,repository,base_sha,failure_class,outcome FROM attempts"
        ).fetchall()


def _version(path) -> tuple[int, str]:
    with sqlite3.connect(path) as db:
        return (
            db.execute("PRAGMA user_version").fetchone()[0],
            db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],
        )


def _v1_file(root):
    memory.record_attempt(root, _attempt_packet("P1"), _attempt_result(), "FAIL")
    path = memory.db_path(root)
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE strategy_exposures")
        db.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
        db.execute("PRAGMA user_version=1")
    return path


def test_v1_migrates_in_place_without_losing_immutable_attempt(tmp_path):
    path = _v1_file(tmp_path)
    before = _rows(path)
    assert _version(path) == (1, "1")
    assert memory.stats(tmp_path)["attempts"] == 1
    assert _version(path) == (2, "2")
    assert _rows(path) == before
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE name='strategy_exposures'"
        ).fetchone() == ("strategy_exposures",)


def test_historical_v2_marker_with_unset_pragma_version_is_supported(tmp_path):
    memory.record_attempt(tmp_path, _attempt_packet("P1"), _attempt_result(), "FAIL")
    path = memory.db_path(tmp_path)
    before = _rows(path)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=0")
    assert _version(path) == (0, "2")
    assert memory.stats(tmp_path)["attempts"] == 1
    assert _version(path) == (2, "2")
    assert _rows(path) == before


@pytest.mark.parametrize("marker,version,error", [
    ("3", 0, "DEVELOPMENT_MEMORY_UNSUPPORTED_SCHEMA"),
    ("1", 2, "DEVELOPMENT_MEMORY_SCHEMA_VERSION_CONFLICT"),
])
def test_unknown_or_conflicting_schema_markers_never_get_overwritten(
        tmp_path, marker, version, error):
    memory.record_attempt(tmp_path, _attempt_packet("P1"), _attempt_result(), "FAIL")
    path = memory.db_path(tmp_path)
    before = _rows(path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE meta SET value=?", (marker,))
        db.execute(f"PRAGMA user_version={version}")
    with pytest.raises(RuntimeError, match=error):
        memory.stats(tmp_path)
    assert _version(path) == (version, marker)
    assert _rows(path) == before


def test_missing_v2_table_is_not_silently_recreated(tmp_path):
    memory.record_attempt(tmp_path, _attempt_packet("P1"), _attempt_result(), "FAIL")
    path = memory.db_path(tmp_path)
    before = _rows(path)
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE strategy_exposures")
    with pytest.raises(RuntimeError, match="DEVELOPMENT_MEMORY_SCHEMA_VERSION_CONFLICT"):
        memory.stats(tmp_path)
    assert _rows(path) == before
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='strategy_exposures'"
        ).fetchone() is None


def test_rollback_of_aborted_schema_upgrade_does_not_leave_partial_table(tmp_path):
    path = _v1_file(tmp_path)
    before = _rows(path)
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TRIGGER block_upgrade BEFORE UPDATE ON meta
                    BEGIN SELECT RAISE(ABORT,'simulated upgrade interruption'); END""")
    with pytest.raises(sqlite3.DatabaseError, match="simulated upgrade interruption"):
        memory.stats(tmp_path)
    assert _version(path) == (1, "1")
    assert _rows(path) == before
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='strategy_exposures'"
        ).fetchone() is None
        db.execute("DROP TRIGGER block_upgrade")
    assert memory.stats(tmp_path)["attempts"] == 1
    assert _version(path) == (2, "2")


def test_concurrent_migration_and_same_packet_replay_serialize(tmp_path):
    path = _v1_file(tmp_path)
    packet = _attempt_packet("P2")
    result = _attempt_result()

    def record(_):
        return memory.record_attempt(tmp_path, packet, result, "FAIL")

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(record, range(12)))
    assert outcomes == ["SLICE_TESTS_FAILED"] * 12
    assert _version(path) == (2, "2")
    assert {r[0] for r in _rows(path)} == {"P1", "P2"}
    assert len(_rows(path)) == 2


def _plan(root):
    data = {
        "schema_version": 1, "parent_id": PARENT,
        "repository": REPO, "roadmap_ref": "docs/OWNER_APPROVED_ROADMAP_WEB.md",
        "planned_at_head": "a" * 40, "status": "ACTIVE",
        "coverage": {"1": ["t1"]},
        "subtasks": [{"key": "t1", "depends_on": [], "module_path": "pkg/foo.py"}],
    }
    atomic_json(root / "decompositions" / f"{PARENT}.json", data)
    return data


def test_checkpoint_concurrent_writers_preserve_one_immutable_receipt(tmp_path):
    plan = _plan(tmp_path)
    item = {"id": PARENT + "-t1", "repository": REPO, "status": "approved", "level": 1}
    state = {"items": {item["id"]: {"outcome": "MERGED", "packets": ["PACKET-1"]}}}

    def record(_):
        return progress.record_checkpoints(tmp_path, REPO, "b" * 40, [item], state)

    with ThreadPoolExecutor(max_workers=8) as pool:
        result = list(pool.map(record, range(16)))
    assert result.count([PARENT]) == 1
    assert result.count([]) == 15
    path = progress.checkpoint_path(tmp_path, plan)
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["receipts"][0]["receipt"] == "GITHUB_MERGED_IN_LANE_LEDGER"
    assert len(stored["receipts"]) == 1
    assert progress.record_checkpoints(tmp_path, REPO, "b" * 40, [item], state) == []
    assert len(json.loads(path.read_text(encoding="utf-8"))["receipts"]) == 1


def test_checkpoint_crash_like_staging_file_never_overwrites_canonical_receipt(tmp_path):
    plan = _plan(tmp_path)
    item = {"id": PARENT + "-t1", "repository": REPO, "status": "approved", "level": 1}
    state = {"items": {item["id"]: {"outcome": "MERGED", "packets": ["P1"]}}}
    progress.record_checkpoints(tmp_path, REPO, "b" * 40, [item], state)
    path = progress.checkpoint_path(tmp_path, plan)
    before = path.read_bytes()
    path.with_name(path.name + ".tmp.aborted").write_text(
        '{"partial":', encoding="utf-8"
    )
    assert progress.record_checkpoints(tmp_path, REPO, "b" * 40, [item], state) == []
    assert path.read_bytes() == before


def test_orphaned_strategy_exposure_is_not_trusted_as_learning(tmp_path):
    memory.record_attempt(tmp_path, _attempt_packet("P1"), _attempt_result(), "FAIL")
    path = memory.db_path(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute(
            "INSERT INTO strategy_exposures VALUES (?,?,?,?)",
            ("ghost-packet", "SLICE_TESTS_FAILED", 1, "P1"),
        )
    with pytest.raises(RuntimeError, match="DEVELOPMENT_MEMORY_INTEGRITY_FAILED"):
        memory.stats(tmp_path)
    # The source of the diagnostic stays intact for manual recovery.
    assert len(_rows(path)) == 1


def test_unrelated_sqlite_database_is_not_claimed_as_new_development_memory(tmp_path):
    path = memory.db_path(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE unrelated_data (important TEXT)")
        db.execute("INSERT INTO unrelated_data VALUES ('keep this')")
    with pytest.raises(RuntimeError, match="DEVELOPMENT_MEMORY_UNKNOWN_SCHEMA"):
        memory.stats(tmp_path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT important FROM unrelated_data").fetchone() == (
            "keep this",
        )
        assert db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='attempts'"
        ).fetchone() is None


def test_versioned_database_without_its_tables_is_not_treated_as_empty(tmp_path):
    path = memory.db_path(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=1")
    with pytest.raises(RuntimeError, match="DEVELOPMENT_MEMORY_PARTIAL_SCHEMA"):
        memory.stats(tmp_path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='attempts'"
        ).fetchone() is None


def test_guided_attempt_and_strategy_exposure_roll_back_together(tmp_path):
    """A worker death between correlated writes cannot poison replay."""
    memory.record_attempt(tmp_path, _attempt_packet("P1"), _attempt_result(), "FAIL")
    packet = {
        **_attempt_packet("P2"),
        "slice": {"id": "RM-web-s1", "build": {"function_name": "f"}},
        "recovery_strategy": {
            "schema_version": 1, "strategy": "SLICE_TESTS_FAILED",
            "revision": 1, "evidence_packet": "P1",
        },
    }
    result = {"status": "PASS", "summary": "ok"}
    path = memory.db_path(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TRIGGER fail_exposure BEFORE INSERT ON strategy_exposures
                      BEGIN SELECT RAISE(ABORT, 'simulated crash after attempt insert'); END""")
    with pytest.raises(sqlite3.DatabaseError, match="simulated crash"):
        memory.record_attempt(tmp_path, packet, result, "ACCEPTED_LOCAL")
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM attempts WHERE packet_id='P2'"
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM strategy_exposures WHERE packet_id='P2'"
        ).fetchone()[0] == 0
        db.execute("DROP TRIGGER fail_exposure")

    assert memory.record_attempt(tmp_path, packet, result, "ACCEPTED_LOCAL") == "PASSED"
    assert memory.record_attempt(tmp_path, packet, result, "ACCEPTED_LOCAL") == "PASSED"
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM attempts WHERE packet_id='P2'"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT strategy,evidence_packet FROM strategy_exposures WHERE packet_id='P2'"
        ).fetchone() == ("SLICE_TESTS_FAILED", "P1")
