"""Durable non-executing Iron God desktop request protocol regression tests."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import pytest

from project_orchestrator.iron_god_desktop_requests import (
    DesktopRequestError,
    get_request,
    install_request_table,
    record_observation,
    submit_request,
)

NOW = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)
GEN = "real-generation-id"
SHA = "a" * 40
ID = "1" * 32


@pytest.fixture
def db(tmp_path):
    # Same DB/file as the existing local dispatch ledger; no parallel
    # database and no user_version migration.
    path = tmp_path / "existing-dispatch-claims.sqlite"
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        "CREATE TABLE other_operational_state (id INTEGER PRIMARY KEY, tag TEXT)"
    )
    connection.execute("INSERT INTO other_operational_state (tag) VALUES ('preserve')")
    connection.execute("PRAGMA user_version=2")
    install_request_table(connection)
    yield connection
    connection.close()


def test_pause_request_durable_without_owner_authority(db):
    request = submit_request(db, action="GIVE_ME_CONTROL", command_id=ID, now=NOW)
    assert request.action == "GIVE_ME_CONTROL"
    assert request.state == "REQUESTED_NOT_AUTHORIZED"
    assert request.execution_authorized is False
    assert request.observer_generation is None
    assert get_request(db, ID) == request
    assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    assert (
        db.execute("SELECT tag FROM other_operational_state").fetchone()[0]
        == "preserve"
    )


def test_resume_request_is_only_intent_not_a_resume(db):
    request = submit_request(db, action="TAKE_THE_WHEEL", command_id=ID, now=NOW)
    assert request.state == "REQUESTED_NOT_AUTHORIZED"
    assert request.execution_authorized is False
    assert "authorized" not in asdict(request)  # no caller-settable authority field


def test_request_is_visible_to_an_independent_database_reader(db):
    submitted = submit_request(db, action="GIVE_ME_CONTROL", command_id=ID, now=NOW)
    other = sqlite3.connect(
        db.execute("PRAGMA database_list").fetchone()[2], isolation_level=None
    )
    try:
        assert get_request(other, submitted.command_id) == submitted
    finally:
        other.close()


def test_observation_is_not_command_completion_or_execution_grant(db):
    submit_request(db, action="TAKE_THE_WHEEL", command_id=ID, now=NOW)
    observed = record_observation(
        db,
        command_id=ID,
        observer_generation=GEN,
        observer_release_sha=SHA,
        now=NOW + timedelta(seconds=1),
    )
    assert observed.state == "OBSERVED_NOT_AUTHORIZED"
    assert observed.execution_authorized is False
    assert observed.observer_generation == GEN
    assert observed.observer_release_sha == SHA
    assert get_request(db, ID) == observed
    assert (
        db.execute("SELECT tag FROM other_operational_state").fetchone()[0]
        == "preserve"
    )


def test_duplicate_request_is_fenced_without_changing_first_request(db):
    first = submit_request(db, action="GIVE_ME_CONTROL", command_id=ID, now=NOW)
    with pytest.raises(DesktopRequestError, match="COMMAND_ALREADY_EXISTS"):
        submit_request(db, action="TAKE_THE_WHEEL", command_id=ID, now=NOW)
    assert get_request(db, ID) == first


def test_concurrent_duplicate_from_other_connection_is_rejected(db):
    path = db.execute("PRAGMA database_list").fetchone()[2]
    submit_request(db, action="GIVE_ME_CONTROL", command_id=ID, now=NOW)
    other = sqlite3.connect(path, isolation_level=None)
    try:
        with pytest.raises(DesktopRequestError, match="COMMAND_ALREADY_EXISTS"):
            submit_request(other, action="GIVE_ME_CONTROL", command_id=ID, now=NOW)
    finally:
        other.close()


def test_same_observation_idempotent_but_wrong_generation_conflicts(db):
    submit_request(db, action="GIVE_ME_CONTROL", command_id=ID, now=NOW)
    first = record_observation(
        db,
        command_id=ID,
        observer_generation=GEN,
        observer_release_sha=SHA,
        now=NOW + timedelta(seconds=1),
    )
    again = record_observation(
        db,
        command_id=ID,
        observer_generation=GEN,
        observer_release_sha=SHA,
        now=NOW + timedelta(seconds=2),
    )
    assert first == again
    with pytest.raises(DesktopRequestError, match="OBSERVATION_IDENTITY_CONFLICT"):
        record_observation(
            db,
            command_id=ID,
            observer_generation="restarted",
            observer_release_sha=SHA,
            now=NOW + timedelta(seconds=3),
        )
    assert get_request(db, ID) == first


def test_stale_or_future_observation_cannot_write_success(db):
    request = submit_request(db, action="TAKE_THE_WHEEL", command_id=ID, now=NOW)
    for elapsed in (-1, 121):
        with pytest.raises(DesktopRequestError, match="COMMAND_NOT_FRESH"):
            record_observation(
                db,
                command_id=ID,
                observer_generation=GEN,
                observer_release_sha=SHA,
                now=NOW + timedelta(seconds=elapsed),
            )
    assert get_request(db, ID) == request


def test_missing_or_invalid_command_observation_fails_closed(db):
    with pytest.raises(DesktopRequestError, match="COMMAND_NOT_FOUND"):
        record_observation(
            db,
            command_id=ID,
            observer_generation=GEN,
            observer_release_sha=SHA,
            now=NOW,
        )
    submit_request(db, action="GIVE_ME_CONTROL", command_id=ID, now=NOW)
    for bad_generation, bad_sha in ((GEN, "invalid"), ("", SHA), ("x" * 129, SHA)):
        with pytest.raises(DesktopRequestError, match="OBSERVER_IDENTITY_UNVERIFIED"):
            record_observation(
                db,
                command_id=ID,
                observer_generation=bad_generation,
                observer_release_sha=bad_sha,
                now=NOW,
            )


def test_invalid_action_id_and_clock_never_write(db):
    for action, identifier in (
        ("ENABLE_WORKERS", ID),
        ("TAKE_THE_WHEEL", "not-a-uuid"),
        ("GIVE_ME_CONTROL", "a" * 40),
    ):
        with pytest.raises(DesktopRequestError):
            submit_request(db, action=action, command_id=identifier, now=NOW)
    with pytest.raises(ValueError, match="timezone-aware"):
        submit_request(
            db, action="GIVE_ME_CONTROL", command_id=ID, now=NOW.replace(tzinfo=None)
        )
    assert (
        db.execute("SELECT COUNT(*) FROM iron_god_desktop_requests").fetchone()[0] == 0
    )


def test_caller_transaction_prevents_nested_mutation(db):
    db.execute("BEGIN")
    try:
        with pytest.raises(DesktopRequestError, match="CALLER_TRANSACTION_ACTIVE"):
            submit_request(db, action="GIVE_ME_CONTROL", command_id=ID, now=NOW)
    finally:
        db.execute("ROLLBACK")


def test_newer_pause_supersedes_unexecuted_resume(db):
    resume = submit_request(db, action="TAKE_THE_WHEEL", command_id=ID, now=NOW)
    pause = submit_request(
        db,
        action="GIVE_ME_CONTROL",
        command_id="2" * 32,
        now=NOW + timedelta(seconds=1),
    )
    with pytest.raises(DesktopRequestError, match="COMMAND_SUPERSEDED"):
        record_observation(
            db,
            command_id=resume.command_id,
            observer_generation=GEN,
            observer_release_sha=SHA,
            now=NOW + timedelta(seconds=2),
        )
    observed_pause = record_observation(
        db,
        command_id=pause.command_id,
        observer_generation=GEN,
        observer_release_sha=SHA,
        now=NOW + timedelta(seconds=2),
    )
    assert observed_pause.state == "OBSERVED_NOT_AUTHORIZED"
    assert get_request(db, ID) == resume


def test_get_request_and_install_never_turn_observation_into_success(db):
    request = submit_request(db, action="GIVE_ME_CONTROL", command_id=ID, now=NOW)
    install_request_table(db)  # idempotent alongside preexisting operational tables
    assert get_request(db, ID) == request
    states = db.execute(
        "SELECT sql FROM sqlite_master WHERE name='iron_god_desktop_requests'"
    ).fetchone()[0]
    assert "REQUESTED_NOT_AUTHORIZED" in states
    assert "OBSERVED_NOT_AUTHORIZED" in states
    assert "APPLIED" not in states
    assert "PAUSED" not in states


def test_cli_request_and_inspection_are_real_cross_process_sqlite_transport(
    tmp_path, monkeypatch, capsys
):
    import json
    from project_orchestrator.iron_god_desktop_requests import main

    path = tmp_path / "existing-iron-god.sqlite"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE acceptance_dispatch_claims (id INTEGER PRIMARY KEY, item TEXT)"
        )
        db.execute("INSERT INTO acceptance_dispatch_claims (item) VALUES ('preserve')")
    assert main(["--database", str(path), "--prepare"]) == 0
    assert (
        json.loads(capsys.readouterr().out)["status"] == "SCHEMA_READY_NOT_AUTHORIZED"
    )

    # Actual CLI path persists its request, but deliberately returns an
    # *unsuccessful desktop-control* exit code: there is no engine ack.
    assert main(["--database", str(path), "--action", "GIVE_ME_CONTROL"]) == 5
    ticket = json.loads(capsys.readouterr().out)
    assert ticket["state"] == "REQUESTED_NOT_AUTHORIZED"
    assert ticket["execution_authorized"] is False
    assert len(ticket["command_id"]) == 32
    assert main(["--database", str(path), "--inspect", ticket["command_id"]]) == 5
    observed = json.loads(capsys.readouterr().out)
    assert observed == ticket

    # Separate SQLite reader: same durable database; no parallel state file.
    with sqlite3.connect(path, isolation_level=None) as other:
        assert get_request(other, ticket["command_id"]).execution_authorized is False
        assert other.execute(
            "SELECT item FROM acceptance_dispatch_claims"
        ).fetchone() == ("preserve",)


def test_cli_fails_closed_on_missing_or_wrong_database(tmp_path, capsys):
    import json
    from project_orchestrator.iron_god_desktop_requests import main

    missing = tmp_path / "must-not-be-created.sqlite"
    assert main(["--database", str(missing), "--prepare"]) == 3
    assert not missing.exists()
    assert json.loads(capsys.readouterr().out)["status"] == "EXISTING_DATABASE_REQUIRED"

    ordinary = tmp_path / "not-iron-god.sqlite"
    with sqlite3.connect(ordinary) as db:
        db.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
    assert main(["--database", str(ordinary), "--prepare"]) == 3
    assert (
        json.loads(capsys.readouterr().out)["status"] == "REQUEST_TRANSPORT_UNVERIFIED"
    )
    with sqlite3.connect(ordinary) as db:
        assert (
            db.execute(
                "SELECT count(*) FROM sqlite_master "
                "WHERE name='iron_god_desktop_requests'"
            ).fetchone()[0]
            == 0
        )


def test_cli_executable_exit_code_never_claims_the_button_worked(tmp_path):
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    path = tmp_path / "existing.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE acceptance_dispatch_claims (id INTEGER PRIMARY KEY)")
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "src")

    def invoke(*more):
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "project_orchestrator.iron_god_desktop_requests",
                "--database",
                str(path),
                *more,
            ],
            text=True,
            capture_output=True,
            check=False,
            cwd=repo_root,
            env=env,
            timeout=15,
        )

    prepared = invoke("--prepare")
    assert prepared.returncode == 0, prepared.stderr
    requested = invoke("--action", "TAKE_THE_WHEEL")
    assert requested.returncode == 5, requested.stderr
    record = json.loads(requested.stdout)
    assert record["state"] == "REQUESTED_NOT_AUTHORIZED"
    assert record["execution_authorized"] is False
    checked = invoke("--inspect", record["command_id"])
    assert checked.returncode == 5, checked.stderr
    assert json.loads(checked.stdout) == record


def test_real_dispatch_claim_schema_coexists_in_one_sqlite_store(tmp_path):
    from project_orchestrator.local_dispatch_event_claim import _create_v2_schema

    path = tmp_path / "claims.sqlite"
    with sqlite3.connect(path, isolation_level=None) as db:
        _create_v2_schema(db)
        db.execute("PRAGMA user_version=2")
        before = tuple(
            db.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE name LIKE '%dispatch_claims%' OR name LIKE 'ux_active_%' "
                "ORDER BY name"
            ).fetchall()
        )
        install_request_table(db)
        ticket = submit_request(db, action="GIVE_ME_CONTROL", now=NOW)
        assert ticket.execution_authorized is False
        assert get_request(db, ticket.command_id) == ticket
        after = tuple(
            db.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE name LIKE '%dispatch_claims%' OR name LIKE 'ux_active_%' "
                "ORDER BY name"
            ).fetchall()
        )
        assert before == after
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
