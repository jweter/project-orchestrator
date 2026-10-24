"""Supervisor-side observational consumption; never implies worker authorization."""

from __future__ import annotations

import ast
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from project_orchestrator.iron_god_desktop_requests import (
    get_request,
    install_request_table,
    submit_request,
)
from project_orchestrator.iron_god_supervisor_request_observer import (
    observe_next_desktop_request,
)

NOW = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)
GENERATION = "1" * 32
SHA = "a" * 40
C1 = "2" * 32
C2 = "3" * 32


@pytest.fixture
def db_path(tmp_path: Path):
    path = tmp_path / "iron-god-dispatch-claims.sqlite"
    with sqlite3.connect(path, isolation_level=None) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute(
            "CREATE TABLE acceptance_dispatch_claims "
            "(id INTEGER PRIMARY KEY, accepted_sha TEXT)"
        )
        db.execute(
            "INSERT INTO acceptance_dispatch_claims(accepted_sha) VALUES (?)",
            ("f" * 40,),
        )
        db.execute(
            "CREATE TABLE controller_shadow_state "
            "(mode TEXT, candidate_execution_authorized INTEGER)"
        )
        db.execute(
            "INSERT INTO controller_shadow_state VALUES ('shadow_state_only', 0)"
        )
        install_request_table(db)
    return path


def poll(path: Path | None, **kwargs):
    return observe_next_desktop_request(
        claims_db=path,
        supervisor_generation=GENERATION,
        release_sha=SHA,
        now=NOW + timedelta(seconds=2),
        **kwargs,
    )


def test_default_off_does_not_touch_disk(tmp_path):
    assert poll(None).status == "NOT_CONFIGURED"
    assert list(tmp_path.iterdir()) == []


def test_uninstalled_claim_store_is_not_created(tmp_path):
    missing = tmp_path / "never-create-me.sqlite"
    result = poll(missing)
    assert result.status == "CLAIMS_STORE_UNAVAILABLE"
    assert not missing.exists()


def test_existing_claims_without_request_table_rejected_without_schema_changes(
    tmp_path,
):
    path = tmp_path / "claims.sqlite"
    with sqlite3.connect(path, isolation_level=None) as db:
        db.execute("CREATE TABLE acceptance_dispatch_claims (id INTEGER PRIMARY KEY)")
    result = poll(path)
    assert result.status == "REQUEST_TRANSPORT_NOT_INSTALLED"
    with sqlite3.connect(path) as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE name='iron_god_desktop_requests'"
            ).fetchone()[0]
            == 0
        )


def test_no_request_is_not_an_acknowledgment(db_path):
    result = poll(db_path)
    assert result.status == "NO_REQUEST"
    assert result.execution_authorized is False
    assert result.command_id is None


def test_actual_observation_is_durable_and_not_authorizing(db_path):
    with sqlite3.connect(db_path, isolation_level=None) as db:
        submit_request(db, action="TAKE_THE_WHEEL", command_id=C1, now=NOW)
    result = poll(db_path)
    assert result.status == "OBSERVED_NOT_AUTHORIZED"
    assert result.command_id == C1
    assert result.action == "TAKE_THE_WHEEL"
    assert result.observer_generation == GENERATION
    assert result.execution_authorized is False
    # A separate reader confirms receipt durability and untouched shadow mode.
    with sqlite3.connect(db_path, isolation_level=None) as other:
        ticket = get_request(other, C1)
        assert ticket is not None
        assert ticket.state == "OBSERVED_NOT_AUTHORIZED"
        assert ticket.observer_release_sha == SHA
        assert ticket.execution_authorized is False
        assert other.execute(
            "SELECT mode, candidate_execution_authorized FROM controller_shadow_state"
        ).fetchone() == ("shadow_state_only", 0)
        assert other.execute(
            "SELECT accepted_sha FROM acceptance_dispatch_claims"
        ).fetchone() == ("f" * 40,)


def test_idempotent_reread_does_not_make_success(db_path):
    with sqlite3.connect(db_path, isolation_level=None) as db:
        submit_request(db, action="GIVE_ME_CONTROL", command_id=C1, now=NOW)
    first = poll(db_path)
    second = poll(db_path)
    assert first.status == "OBSERVED_NOT_AUTHORIZED"
    assert second.status == "ALREADY_OBSERVED_NOT_AUTHORIZED"
    assert second.execution_authorized is False


def test_last_click_wins_resume_superseded_by_pause(db_path):
    with sqlite3.connect(db_path, isolation_level=None) as db:
        submit_request(db, action="TAKE_THE_WHEEL", command_id=C1, now=NOW)
        submit_request(
            db,
            action="GIVE_ME_CONTROL",
            command_id=C2,
            now=NOW + timedelta(seconds=1),
        )
    result = poll(db_path)
    assert result.command_id == C2
    assert result.action == "GIVE_ME_CONTROL"
    with sqlite3.connect(db_path, isolation_level=None) as db:
        assert get_request(db, C1).state == "REQUESTED_NOT_AUTHORIZED"
        assert get_request(db, C2).state == "OBSERVED_NOT_AUTHORIZED"


def test_stale_and_future_requests_are_never_observed(db_path):
    with sqlite3.connect(db_path, isolation_level=None) as db:
        submit_request(
            db,
            action="TAKE_THE_WHEEL",
            command_id=C1,
            now=NOW - timedelta(seconds=500),
        )
    result = poll(db_path)
    assert result.status == "COMMAND_NOT_FRESH"
    with sqlite3.connect(db_path, isolation_level=None) as db:
        assert get_request(db, C1).state == "REQUESTED_NOT_AUTHORIZED"


def test_bad_generation_and_release_do_not_observe(db_path):
    with sqlite3.connect(db_path, isolation_level=None) as db:
        submit_request(db, action="GIVE_ME_CONTROL", command_id=C1, now=NOW)
    for generation, release, expected in [
        ("", SHA, "SUPERVISOR_GENERATION_UNVERIFIED"),
        ("bad", SHA, "SUPERVISOR_GENERATION_UNVERIFIED"),
        (GENERATION, None, "RELEASE_IDENTITY_UNVERIFIED"),
        (GENERATION, "bad", "RELEASE_IDENTITY_UNVERIFIED"),
    ]:
        result = observe_next_desktop_request(
            claims_db=db_path,
            supervisor_generation=generation,
            release_sha=release,
            now=NOW,
        )
        assert result.status == expected
        assert result.execution_authorized is False
    with sqlite3.connect(db_path, isolation_level=None) as db:
        assert get_request(db, C1).state == "REQUESTED_NOT_AUTHORIZED"


def test_observer_does_not_modify_existing_scheduled_tasks_or_state(db_path):
    # Explicitly no calls into PowerShell, owner marker, or launcher code.
    import inspect
    from project_orchestrator import iron_god_supervisor_request_observer as module

    source = inspect.getsource(module)
    for forbidden in (
        "subprocess",
        "powershell",
        "Start-ScheduledTask",
        "human-mode.json",
        "candidate_execution_authorized =",
        "os.system",
    ):
        assert forbidden not in source
    assert poll(db_path).status == "NO_REQUEST"


def test_real_supervisor_flag_default_is_off_and_observation_precedes_dispatch():
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "continuous_flow_supervisor.py"
    )
    source = script.read_text(encoding="utf-8")
    ast.parse(source)
    assert 'p.add_argument("--desktop-requests-db",type=Path,default=None' in source
    assert "desktop_observation=supervisor_observation_snapshot(" in source
    assert source.index(
        "if human_mode_active(state):", source.index("while True:")
    ) < source.index("desktop_observation=supervisor_observation_snapshot(")
    assert source.index(
        "desktop_observation=supervisor_observation_snapshot("
    ) < source.index(
        "dispatched_workers=dispatch_parallel_builders(", source.index("while True:")
    )
    assert '"desktop_request_observation":desktop_observation' in source


def test_claims_db_not_an_sqlite_database_is_rejected_without_crash(tmp_path):
    file = tmp_path / "invalid.sqlite"
    file.write_text("not sqlite data", encoding="utf-8")
    assert poll(file).status == "CLAIMS_STORE_QUERY_FAILED"


def test_supervisor_snapshot_projection_reports_not_authorized(db_path):
    from project_orchestrator.iron_god_supervisor_request_observer import (
        supervisor_observation_snapshot,
    )

    snapshot = supervisor_observation_snapshot(None, GENERATION, SHA)
    assert snapshot == {
        "status": "NOT_CONFIGURED",
        "command_id": None,
        "action": None,
        "observer_generation": None,
        "execution_authorized": False,
    }
    with sqlite3.connect(db_path, isolation_level=None) as db:
        submit_request(db, action="GIVE_ME_CONTROL", command_id=C1)
    snapshot = supervisor_observation_snapshot(db_path, GENERATION, SHA)
    assert snapshot["status"] == "OBSERVED_NOT_AUTHORIZED"
    assert snapshot["command_id"] == C1
    assert snapshot["execution_authorized"] is False
