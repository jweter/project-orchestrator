"""RDI-001 resident authority admission never creates a state DB."""
import sqlite3

import pytest

from project_orchestrator import resident_dispatch_authority as admission
from project_orchestrator.local_first_state import LocalFirstState


@pytest.fixture
def authorized(monkeypatch):
    monkeypatch.setattr(admission, "owner_control_status", lambda path: admission.AUTHORIZED)


def check(tmp_path, **overrides):
    args = dict(
        controller={"mode": "candidate_mode", "candidate_execution_authorized": True},
        owner_state_root=tmp_path,
        expected_owner_state_root=tmp_path,
        state_db=tmp_path / "missing.sqlite",
        repo=tmp_path,
    )
    args.update(overrides)
    if isinstance(args["controller"], dict):
        args["controller"] = {
            "controller": "iron-god-handoff-v1",
            "slices": [],
            **args["controller"],
        }
    return admission.require_existing_dispatch_authority(**args)


@pytest.mark.parametrize(
    "controller",
    [
        {"controller": "untrusted", "mode": "candidate_mode",
         "candidate_execution_authorized": True},
        {"slices": None, "mode": "candidate_mode",
         "candidate_execution_authorized": True},
        None,
    ],
)
def test_unverified_controller_identity_never_opens_database(
    tmp_path, authorized, controller
):
    with pytest.raises(
        admission.ResidentDispatchBlocked, match="CONTROLLER_IDENTITY_UNVERIFIED"
    ):
        check(tmp_path, controller=controller)
    assert not (tmp_path / "missing.sqlite").exists()


def test_missing_db_is_blocked_without_creation(tmp_path, authorized):
    with pytest.raises(admission.ResidentDispatchBlocked, match="ACCEPTANCE_STATE_DB_MISSING_OR_UNTRUSTED"):
        check(tmp_path)
    assert not (tmp_path / "missing.sqlite").exists()


def test_shadow_mode_is_blocked(tmp_path, authorized):
    with pytest.raises(admission.ResidentDispatchBlocked, match="CANDIDATE_EXECUTION_NOT_AUTHORIZED"):
        check(tmp_path, controller={"mode": "shadow_state_only", "candidate_execution_authorized": False})


@pytest.mark.parametrize(
    "controller",
    [
        {"mode": "candidate_mode", "candidate_execution_authorized": False},
        {"mode": "candidate_mode", "candidate_execution_authorized": "true"},
        {"mode": "candidate_mode"},
        {"mode": "shadow_state_only", "candidate_execution_authorized": True},
    ],
)
def test_candidate_authority_requires_both_exact_flags(tmp_path, authorized, controller):
    with pytest.raises(
        admission.ResidentDispatchBlocked,
        match="CANDIDATE_EXECUTION_NOT_AUTHORIZED",
    ):
        check(tmp_path, controller=controller)
    assert not (tmp_path / "missing.sqlite").exists()


def test_owner_veto_precedes_database_access(tmp_path, monkeypatch):
    monkeypatch.setattr(admission, "owner_control_status", lambda path: "BLOCKED")
    with pytest.raises(admission.ResidentDispatchBlocked, match="OWNER_CONTROL_VETO"):
        check(tmp_path)


def test_empty_sqlite_is_not_authoritative(tmp_path, authorized):
    db = tmp_path / "existing.sqlite"
    sqlite3.connect(db).close()
    with pytest.raises(admission.ResidentDispatchBlocked, match="ACCEPTANCE_STATE_SCHEMA_UNVERIFIED"):
        check(tmp_path, state_db=db)


def test_wrong_owner_root_is_blocked(tmp_path, authorized):
    with pytest.raises(admission.ResidentDispatchBlocked, match="OWNER_AUTHORITY_ROOT_MISMATCH"):
        check(tmp_path, expected_owner_state_root=tmp_path / "other")


def test_version_two_without_authority_tables_is_blocked(tmp_path, authorized):
    db = tmp_path / "schema_only.sqlite"
    with sqlite3.connect(db) as connection:
        connection.execute("PRAGMA user_version = 2")
    with pytest.raises(admission.ResidentDispatchBlocked, match="ACCEPTANCE_STATE_TABLES_UNVERIFIED"):
        check(tmp_path, state_db=db)


def test_spoofed_authority_table_names_are_not_enough(tmp_path, authorized):
    db = tmp_path / "spoofed.sqlite"
    with sqlite3.connect(db) as connection:
        for table in ("milestones", "acceptance_intents", "journal"):
            connection.execute(f"CREATE TABLE {table} (fake_column TEXT)")
        connection.execute("PRAGMA user_version = 2")
    with pytest.raises(
        admission.ResidentDispatchBlocked,
        match="ACCEPTANCE_STATE_SCHEMA_UNVERIFIED",
    ):
        check(tmp_path, state_db=db)


def test_spoofed_columns_without_constraints_are_not_authoritative(
    tmp_path, authorized
):
    db = tmp_path / "weak_schema.sqlite"
    tables = {
        "milestones": (
            "milestone_id", "repository", "accepted_sha",
            "verification_sha256", "product_reality", "accepted_at",
        ),
        "acceptance_intents": (
            "milestone_id", "repository", "accepted_base_sha", "accepted_sha",
            "verification_sha256", "product_reality", "context_json",
            "state", "created_at", "changed_at",
        ),
        "journal": ("event_id", "milestone_id", "event_type", "detail", "occurred_at"),
    }
    with sqlite3.connect(db) as connection:
        for table, columns in tables.items():
            declarations = ", ".join(
                f"{column} {'INTEGER' if column == 'event_id' else 'TEXT'}"
                for column in columns
            )
            connection.execute(f"CREATE TABLE {table} ({declarations})")
        connection.execute("PRAGMA user_version = 2")
    with pytest.raises(
        admission.ResidentDispatchBlocked,
        match="ACCEPTANCE_STATE_SCHEMA_UNVERIFIED",
    ):
        check(tmp_path, state_db=db)


def test_real_local_first_schema_is_admitted(tmp_path, authorized):
    db = tmp_path / "valid.sqlite"
    LocalFirstState(db)
    assert check(tmp_path, state_db=db) is None


def test_corrupt_authority_ledger_fails_closed(tmp_path, authorized, monkeypatch):
    db = tmp_path / "corrupt.sqlite"
    LocalFirstState(db)
    connect = sqlite3.connect
    closed = []

    class CorruptProbe:
        def __init__(self, inner):
            self.inner = inner

        def execute(self, sql, *args, **kwargs):
            if sql == "PRAGMA quick_check":
                return self
            return self.inner.execute(sql, *args, **kwargs)

        def fetchone(self):
            return ("database disk image is malformed",)

        def close(self):
            self.inner.close()
            closed.append(True)

    monkeypatch.setattr(
        admission.sqlite3, "connect",
        lambda *args, **kwargs: CorruptProbe(connect(*args, **kwargs)),
    )
    with pytest.raises(
        admission.ResidentDispatchBlocked,
        match="ACCEPTANCE_STATE_INTEGRITY_UNVERIFIED",
    ):
        check(tmp_path, state_db=db)
    assert closed == [True]


def test_non_sqlite_authority_file_fails_closed(tmp_path, authorized):
    db = tmp_path / "not_sqlite.sqlite"
    original = b"not an SQLite authority ledger"
    db.write_bytes(original)
    with pytest.raises(admission.ResidentDispatchBlocked, match="ACCEPTANCE_STATE_UNREADABLE"):
        check(tmp_path, state_db=db)
    assert db.read_bytes() == original


def test_missing_repository_fails_closed(tmp_path, authorized):
    db = tmp_path / "valid.sqlite"
    LocalFirstState(db)
    with pytest.raises(admission.ResidentDispatchBlocked, match="REPOSITORY_NOT_FOUND"):
        check(tmp_path, state_db=db, repo=tmp_path / "missing_repo")


@pytest.mark.parametrize("valid", [True, False])
def test_read_only_authority_probe_closes_connection_on_all_paths(
    tmp_path, authorized, monkeypatch, valid
):
    db = tmp_path / "probe.sqlite"
    if valid:
        LocalFirstState(db)
    else:
        db.write_bytes(b"invalid sqlite database")
    actual_connect = sqlite3.connect
    closed = []

    class TrackedConnection:
        def __init__(self, inner):
            self.inner = inner

        def execute(self, *args, **kwargs):
            return self.inner.execute(*args, **kwargs)

        def close(self):
            self.inner.close()
            closed.append(True)

    monkeypatch.setattr(
        admission.sqlite3, "connect",
        lambda *args, **kwargs: TrackedConnection(actual_connect(*args, **kwargs)),
    )
    if valid:
        assert check(tmp_path, state_db=db) is None
    else:
        with pytest.raises(
            admission.ResidentDispatchBlocked, match="ACCEPTANCE_STATE_UNREADABLE"
        ):
            check(tmp_path, state_db=db)
    assert closed == [True]
