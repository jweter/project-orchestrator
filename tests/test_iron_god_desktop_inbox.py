"""Supervisor inbox is read only; unacknowledged request is not execution."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from project_orchestrator.iron_god_desktop_inbox import (
    inspect_inbox,
    latest_request,
    latest_unobserved,
)
from project_orchestrator.iron_god_desktop_requests import (
    install_request_table,
    record_observation,
    submit_request,
)

NOW = datetime(2026, 10, 24, 17, 0, tzinfo=UTC)
GEN = "local-generation"
SHA = "a" * 40


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(tmp_path / "claims.sqlite", isolation_level=None)
    install_request_table(conn)
    yield conn
    conn.close()


def test_s01_read_latest_not_misrepresent_any_history(db):
    assert latest_request(db) is None
    first = submit_request(db, action="TAKE_THE_WHEEL", now=NOW)
    assert latest_request(db) == first
    second = submit_request(
        db, action="GIVE_ME_CONTROL", now=NOW + timedelta(seconds=1)
    )
    assert latest_request(db) == second
    assert latest_request(db) != first


def test_s02_newest_click_exclusively_eligible_for_shadow_consumption(db):
    submit_request(db, action="TAKE_THE_WHEEL", now=NOW)
    current = submit_request(
        db, action="GIVE_ME_CONTROL", now=NOW + timedelta(seconds=1)
    )
    assert latest_unobserved(db) == current
    record_observation(
        db,
        command_id=current.command_id,
        observer_generation=GEN,
        observer_release_sha=SHA,
        now=NOW + timedelta(seconds=2),
    )
    assert latest_unobserved(db) is None


def test_s03_fresh_pending_request_never_authorizes_execution(db):
    ticket = submit_request(db, action="TAKE_THE_WHEEL", now=NOW)
    decision = inspect_inbox(db, now=NOW + timedelta(seconds=1))
    assert decision.status == "PENDING_UNAUTHORIZED"
    assert decision.request == ticket
    assert decision.execution_authorized is False


def test_s04_expired_and_future_requests_fail_closed(db):
    ticket = submit_request(db, action="GIVE_ME_CONTROL", now=NOW)
    assert inspect_inbox(db, now=NOW - timedelta(seconds=1)).status == "FUTURE_REQUEST"
    assert (
        inspect_inbox(db, now=NOW + timedelta(seconds=121)).status == "EXPIRED_REQUEST"
    )
    assert latest_request(db) == ticket


def test_s05_observed_request_does_not_turn_into_applied_ack(db):
    assert inspect_inbox(db, now=NOW).status == "NO_REQUEST"
    ticket = submit_request(db, action="GIVE_ME_CONTROL", now=NOW)
    record_observation(
        db,
        command_id=ticket.command_id,
        observer_generation=GEN,
        observer_release_sha=SHA,
        now=NOW + timedelta(seconds=5),
    )
    decision = inspect_inbox(db, now=NOW + timedelta(seconds=6))
    assert decision.status == "ALREADY_OBSERVED_NOT_AUTHORIZED"
    assert decision.execution_authorized is False


def test_s06_recent_request_listing_bounded_and_ordered(db):
    from project_orchestrator.iron_god_desktop_inbox import recent_requests

    ids = []
    for i in range(4):
        ticket = submit_request(
            db,
            action="GIVE_ME_CONTROL" if i % 2 else "TAKE_THE_WHEEL",
            now=NOW + timedelta(seconds=i),
        )
        ids.append(ticket.command_id)
    items = recent_requests(db, limit=2)
    assert [item.command_id for item in items] == ids[-1:-3:-1]
    for bad_limit in (0, 101, True, "1", -1):
        with pytest.raises(ValueError, match="limit"):
            recent_requests(db, limit=bad_limit)


def test_s07_diagnostic_action_filter_does_not_override_newest_owner_click(db):
    from project_orchestrator.iron_god_desktop_inbox import latest_matching_action

    resume = submit_request(db, action="TAKE_THE_WHEEL", now=NOW)
    pause = submit_request(db, action="GIVE_ME_CONTROL", now=NOW + timedelta(seconds=1))
    assert latest_matching_action(db, "TAKE_THE_WHEEL") == resume
    assert latest_request(db) == pause
    assert latest_unobserved(db) == pause
    with pytest.raises(ValueError, match="unrecognized"):
        latest_matching_action(db, "ENABLE_UNATTENDED")


def test_s08_count_pending_observed_and_total_without_authority(db):
    from project_orchestrator.iron_god_desktop_inbox import queue_depth

    assert queue_depth(db).total == 0
    submit_request(db, action="GIVE_ME_CONTROL", now=NOW)
    current = submit_request(
        db, action="TAKE_THE_WHEEL", now=NOW + timedelta(seconds=1)
    )
    record_observation(
        db,
        command_id=current.command_id,
        observer_generation=GEN,
        observer_release_sha=SHA,
        now=NOW + timedelta(seconds=2),
    )
    depth = queue_depth(db)
    assert (depth.pending, depth.observed, depth.total) == (1, 1, 2)
    assert depth.execution_authorized is False


def test_s09_missing_and_malformed_schema_cannot_show_ready(tmp_path):
    from project_orchestrator.iron_god_desktop_inbox import request_table_readiness

    conn = sqlite3.connect(tmp_path / "missing.sqlite", isolation_level=None)
    try:
        assert request_table_readiness(conn).status == "TABLE_MISSING"
        conn.execute("CREATE TABLE iron_god_desktop_requests (command_id TEXT)")
        bad = request_table_readiness(conn)
        assert bad.status == "REQUIRED_COLUMNS_MISSING"
        assert "state" in bad.missing_columns
        assert bad.execution_authorized is False
    finally:
        conn.close()


def test_s10_queue_diagnostics_report_backpressure_without_control(db):
    from project_orchestrator.iron_god_desktop_inbox import (
        inspect_queue,
        request_table_readiness,
    )

    assert request_table_readiness(db).status == "STAGED_SCHEMA_PRESENT_NOT_AUTHORIZED"
    submit_request(db, action="GIVE_ME_CONTROL", now=NOW)
    decision, counts, schema, status = inspect_queue(db, now=NOW, pressure_threshold=2)
    assert decision.status == "PENDING_UNAUTHORIZED"
    assert counts.pending == 1
    assert schema.execution_authorized is False
    assert status == "NOT_AUTHORIZED"
    submit_request(db, action="TAKE_THE_WHEEL", now=NOW + timedelta(seconds=1))
    submit_request(db, action="GIVE_ME_CONTROL", now=NOW + timedelta(seconds=2))
    _, counts, _, status = inspect_queue(
        db, now=NOW + timedelta(seconds=3), pressure_threshold=2
    )
    assert counts.pending == 3
    assert status == "BACKLOG_WARNING"
    with pytest.raises(ValueError, match="threshold"):
        inspect_queue(db, pressure_threshold=0)
