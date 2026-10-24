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
