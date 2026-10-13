"""Fail-closed tests for the IG-014 read-only local state health inspector."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from project_orchestrator.local_first_state import LocalFirstState
from project_orchestrator.local_state_health import inspect_structural_health


REPO = "jweter/VoiceStudio"
OTHER_REPO = "jweter/Everward"
HEAD = "a" * 40
BASE = "b" * 40
RECEIPT = "f" * 64


@pytest.fixture
def store(tmp_path: Path) -> LocalFirstState:
    return LocalFirstState(tmp_path / "state" / "iron-god.sqlite")


def accept(
    store: LocalFirstState, ident: str = "s1", sha: str = HEAD, repository: str = REPO
) -> bool:
    return store.record_accepted_milestone(
        repository=repository,
        milestone_id=ident,
        accepted_sha=sha,
        verification_sha256=RECEIPT,
        product_reality="PASSED",
    )


def _tamper(path: Path, sql: str, params: tuple = ()) -> None:
    """Directly mutate rows, bypassing the application layer and its guards."""
    with sqlite3.connect(path) as db:
        db.execute(sql, params)


def test_normal_durable_acceptance_is_structurally_consistent(store: LocalFirstState):
    assert accept(store, "s1")
    assert accept(store, "s2", sha="b" * 40, repository=OTHER_REPO)
    result = inspect_structural_health(store.path)
    assert result["status"] == "STRUCTURALLY_CONSISTENT"
    assert result["milestone_count"] == 2
    assert result["pending_acceptance_count"] == 0
    assert result["active_publication_count"] == 0
    assert "cannot independently prove" in result["note"]


def test_normal_prepared_and_queued_fixture_is_structurally_consistent(
    store: LocalFirstState,
):
    assert accept(store, "s1")
    store.transition_publication(
        milestone_id="s1", expected_state="READY", next_state="PUSHING"
    )
    assert store.prepare_acceptance(
        repository=OTHER_REPO,
        milestone_id="prepared",
        accepted_base_sha=BASE,
        accepted_sha=HEAD,
        verification_sha256=RECEIPT,
        product_reality="PASSED",
        context_json="{}",
    )
    result = inspect_structural_health(store.path)
    assert result["status"] == "STRUCTURALLY_CONSISTENT"
    assert result["active_publication_count"] == 1
    assert result["pending_acceptance_count"] == 1


def test_inspection_performs_no_mutation(store: LocalFirstState):
    assert accept(store, "s1")
    before = store.snapshot()
    inspect_structural_health(store.path)
    inspect_structural_health(store.path)
    assert store.snapshot() == before


def test_missing_database_fails_closed(tmp_path: Path):
    result = inspect_structural_health(tmp_path / "does-not-exist.sqlite")
    assert result["status"] == "DATABASE_ABSENT"


def test_symlinked_database_path_fails_closed(tmp_path: Path):
    real = tmp_path / "real.sqlite"
    with sqlite3.connect(real) as db:
        db.execute("PRAGMA user_version=2")
    link = tmp_path / "link.sqlite"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlink creation is not permitted in this environment")
    result = inspect_structural_health(link)
    assert result["status"] == "DATABASE_PATH_IS_SYMLINK"


def test_directory_in_place_of_database_fails_closed(tmp_path: Path):
    directory = tmp_path / "state.sqlite"
    directory.mkdir()
    result = inspect_structural_health(directory)
    assert result["status"] == "DATABASE_PATH_NOT_A_FILE"


def test_non_sqlite_file_is_reported_corrupt(tmp_path: Path):
    path = tmp_path / "garbage.sqlite"
    path.write_bytes(b"not a sqlite database at all")
    result = inspect_structural_health(path)
    assert result["status"] == "DATABASE_CORRUPT"


def test_unsupported_schema_version_fails_closed(tmp_path: Path):
    path = tmp_path / "wrong-version.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=1")
    result = inspect_structural_health(path)
    assert result["status"] == "UNSUPPORTED_SCHEMA_VERSION"
    assert result["schema_version"] == 1


def test_missing_required_tables_fails_closed(tmp_path: Path):
    path = tmp_path / "no-tables.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=2")
        db.execute("CREATE TABLE milestones (milestone_id TEXT PRIMARY KEY)")
    result = inspect_structural_health(path)
    assert result["status"] == "REQUIRED_TABLE_MISSING"
    assert "publications" in result["missing_tables"]
    assert "acceptance_intents" in result["missing_tables"]


def test_invalid_publication_state_fails_closed(store: LocalFirstState):
    assert accept(store, "s1")
    _tamper(store.path, "UPDATE publications SET state='BOGUS' WHERE milestone_id='s1'")
    result = inspect_structural_health(store.path)
    assert result["status"] == "INVALID_PUBLICATION_STATE"
    assert result["milestone_id"] == "s1"


def test_orphan_publication_fails_closed(store: LocalFirstState):
    assert accept(store, "s1")
    _tamper(
        store.path,
        "INSERT INTO publications VALUES ('ghost', ?, 'READY', NULL, '2020-01-01T00:00:00+00:00')",
        (REPO,),
    )
    result = inspect_structural_health(store.path)
    assert result["status"] == "ORPHAN_PUBLICATION"
    assert result["milestone_ids"] == ["ghost"]


def test_orphan_milestone_fails_closed(store: LocalFirstState):
    assert accept(store, "s1")
    _tamper(store.path, "DELETE FROM publications WHERE milestone_id='s1'")
    result = inspect_structural_health(store.path)
    assert result["status"] == "ORPHAN_MILESTONE"
    assert result["milestone_ids"] == ["s1"]


def test_duplicate_active_publication_in_one_repo_fails_closed(store: LocalFirstState):
    assert accept(store, "s1")
    assert accept(store, "s2", sha="b" * 40)
    store.transition_publication(
        milestone_id="s1", expected_state="READY", next_state="PUSHING"
    )
    # Bypass the application's own one-active-lane-per-repository guard.
    _tamper(
        store.path, "UPDATE publications SET state='PUSHING' WHERE milestone_id='s2'"
    )
    result = inspect_structural_health(store.path)
    assert result["status"] == "DUPLICATE_ACTIVE_PUBLICATION"
    assert result["repository"] == REPO
    assert result["milestone_ids"] == ["s1", "s2"]


def test_acceptance_intent_mismatch_fails_closed(store: LocalFirstState):
    assert store.prepare_acceptance(
        repository=REPO,
        milestone_id="s1",
        accepted_base_sha=BASE,
        accepted_sha=HEAD,
        verification_sha256=RECEIPT,
        product_reality="PASSED",
        context_json="{}",
    )
    assert store.finish_prepared_acceptance(
        milestone_id="s1", expected_sha=HEAD, expected_verification_sha256=RECEIPT
    )
    _tamper(
        store.path,
        "UPDATE acceptance_intents SET accepted_sha=? WHERE milestone_id='s1'",
        ("c" * 40,),
    )
    result = inspect_structural_health(store.path)
    assert result["status"] == "ACCEPTANCE_INTENT_MISMATCH"
    assert result["milestone_id"] == "s1"


def test_contradictory_prepared_intent_with_existing_milestone_fails_closed(
    store: LocalFirstState,
):
    assert store.prepare_acceptance(
        repository=REPO,
        milestone_id="s1",
        accepted_base_sha=BASE,
        accepted_sha=HEAD,
        verification_sha256=RECEIPT,
        product_reality="PASSED",
        context_json="{}",
    )
    # The application never allows a milestone to exist while its matching
    # intent is still PREPARED; only a tool bypassing it can create this.
    _tamper(
        store.path,
        "INSERT INTO milestones VALUES ('s1', ?, ?, ?, 'PASSED', '2020-01-01T00:00:00+00:00')",
        (REPO, HEAD, RECEIPT),
    )
    _tamper(
        store.path,
        "INSERT INTO publications VALUES ('s1', ?, 'READY', NULL, '2020-01-01T00:00:00+00:00')",
        (REPO,),
    )
    result = inspect_structural_health(store.path)
    assert result["status"] == "ACCEPTANCE_INTENT_CONTRADICTS_MILESTONE"
    assert result["milestone_id"] == "s1"


def test_missing_milestone_accepted_journal_fails_closed(store: LocalFirstState):
    assert accept(store, "s1")
    _tamper(store.path, "DELETE FROM journal WHERE event_type='MILESTONE_ACCEPTED'")
    result = inspect_structural_health(store.path)
    assert result["status"] == "MILESTONE_ACCEPTED_JOURNAL_MISSING"
    assert result["milestone_id"] == "s1"


def test_duplicated_milestone_accepted_journal_fails_closed(store: LocalFirstState):
    assert accept(store, "s1")
    _tamper(
        store.path,
        "INSERT INTO journal(milestone_id,event_type,detail,occurred_at) "
        "VALUES ('s1','MILESTONE_ACCEPTED',?,'2020-01-01T00:00:00+00:00')",
        (HEAD,),
    )
    result = inspect_structural_health(store.path)
    assert result["status"] == "MILESTONE_ACCEPTED_JOURNAL_DUPLICATED"
    assert result["count"] == 2


def test_mismatched_milestone_accepted_journal_fails_closed(store: LocalFirstState):
    assert accept(store, "s1")
    _tamper(
        store.path,
        "UPDATE journal SET detail=? WHERE event_type='MILESTONE_ACCEPTED'",
        ("c" * 40,),
    )
    result = inspect_structural_health(store.path)
    assert result["status"] == "MILESTONE_ACCEPTED_JOURNAL_MISMATCHED"


def _build_disposable_schema_without_constraints(path: Path) -> None:
    """A from-scratch, deliberately under-constrained disposable SQLite file.

    Mirrors the shape of LocalFirstState's schema but omits the unique
    partial index on acceptance_intents, so a malicious/non-conforming
    writer tool could leave two PREPARED intents for the same repository.
    """
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=2")
        db.execute("""
            CREATE TABLE milestones (
                milestone_id TEXT PRIMARY KEY, repository TEXT NOT NULL,
                accepted_sha TEXT NOT NULL, verification_sha256 TEXT NOT NULL,
                product_reality TEXT NOT NULL, accepted_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE publications (
                milestone_id TEXT PRIMARY KEY, repository TEXT NOT NULL,
                state TEXT NOT NULL, last_error TEXT, changed_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE journal (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT, milestone_id TEXT NOT NULL,
                event_type TEXT NOT NULL, detail TEXT NOT NULL, occurred_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE acceptance_intents (
                milestone_id TEXT PRIMARY KEY, repository TEXT NOT NULL,
                accepted_base_sha TEXT NOT NULL, accepted_sha TEXT NOT NULL,
                verification_sha256 TEXT NOT NULL, product_reality TEXT NOT NULL,
                context_json TEXT NOT NULL, state TEXT NOT NULL,
                created_at TEXT NOT NULL, changed_at TEXT NOT NULL
            )
        """)


def test_duplicate_prepared_acceptance_in_one_repo_fails_closed(tmp_path: Path):
    path = tmp_path / "disposable.sqlite"
    _build_disposable_schema_without_constraints(path)
    now = "2020-01-01T00:00:00+00:00"
    with sqlite3.connect(path) as db:
        for ident in ("first", "second"):
            db.execute(
                "INSERT INTO acceptance_intents VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    ident,
                    REPO,
                    BASE,
                    HEAD,
                    RECEIPT,
                    "PASSED",
                    "{}",
                    "PREPARED",
                    now,
                    now,
                ),
            )
    result = inspect_structural_health(path)
    assert result["status"] == "DUPLICATE_PREPARED_ACCEPTANCE"
    assert result["repository"] == REPO
    assert result["milestone_ids"] == ["first", "second"]


def test_unexpected_missing_column_is_reported_blocked(tmp_path: Path):
    """A schema with the right table names but wrong columns must never raise;
    it must surface a clear BLOCKED code instead."""
    path = tmp_path / "wrong-columns.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=2")
        db.execute("CREATE TABLE milestones (milestone_id TEXT PRIMARY KEY)")
        db.execute("CREATE TABLE publications (milestone_id TEXT PRIMARY KEY)")
        db.execute("CREATE TABLE journal (milestone_id TEXT)")
        db.execute("CREATE TABLE acceptance_intents (milestone_id TEXT PRIMARY KEY)")
        db.execute("INSERT INTO milestones VALUES ('s1')")
    result = inspect_structural_health(path)
    assert result["status"] == "BLOCKED_UNEXPECTED_DATABASE_ERROR"
    assert "no such column" in result["error"].lower()
