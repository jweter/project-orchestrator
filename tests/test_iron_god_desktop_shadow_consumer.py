"""Supervisor-side shadow consumption must never simulate command completion."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from project_orchestrator.iron_god_desktop_shadow_consumer import shadow_consume_once
from project_orchestrator.iron_god_desktop_requests import (
    get_request,
    install_request_table,
    submit_request,
)

NOW = datetime(2026, 10, 24, 17, 30, tzinfo=UTC)
SHA = "a" * 40
GENERATION = "local-generation-id"


@pytest.fixture
def scenario(tmp_path):
    db = sqlite3.connect(tmp_path / "claims.sqlite", isolation_level=None)
    install_request_table(db)
    item = submit_request(db, action="GIVE_ME_CONTROL", now=NOW)
    info = {
        "controller": {
            "mode": "shadow_state_only",
            "candidate_execution_authorized": False,
        },
        "owner_authority": "VERIFIED_HELD",
        "supervisor_pin": {
            "enabled": True,
            "accepted_sha": SHA,
            "accepted_repo": "C:/release",
        },
        "actor_pin": {
            "enabled": True,
            "accepted_sha": SHA,
            "accepted_repo": "C:/release",
        },
        "heartbeat": {
            "process_instance_id": GENERATION,
            "actor_revision": SHA,
            "status": "RUNNING_CURRENT",
            "heartbeat_at_utc": NOW.isoformat(),
        },
        "supervisor_process": {
            "pid": 1040,
            "process_instance_id": GENERATION,
            "accepted_sha": SHA,
            "process_start_fingerprint": "windows:123",
        },
        "process_fingerprint_fn": lambda pid: "windows:123" if pid == 1040 else None,
        "owner_verifier_fn": lambda: "VERIFIED_HELD",
        "controller_verifier_fn": lambda: {
            "mode": "shadow_state_only",
            "candidate_execution_authorized": False,
        },
        "release_head_fn": lambda path: SHA if path == "C:/release" else None,
        "now": NOW + timedelta(seconds=1),
    }
    try:
        yield db, item, info
    finally:
        db.close()


def test_s11_owner_authority_unverified_refuses_to_observe(scenario):
    db, item, info = scenario
    info["owner_authority"] = "UNVERIFIED"
    result = shadow_consume_once(db, **info)
    assert result.status == "OBSERVATION_BLOCKED"
    assert "OWNER_AUTHORITY_UNVERIFIED" in result.blockers
    assert get_request(db, item.command_id) == item


def test_s12_active_candidate_mode_can_never_use_shadow_observer(scenario):
    db, item, info = scenario
    info["controller"]["mode"] = "candidate_mode"
    info["controller"]["candidate_execution_authorized"] = True
    result = shadow_consume_once(db, **info)
    assert "NOT_SAFE_SHADOW_MODE" in result.blockers
    assert result.observation_recorded is False
    assert get_request(db, item.command_id) == item


def test_s13_release_pin_mismatch_never_observes_request(scenario):
    db, item, info = scenario
    info["actor_pin"]["accepted_sha"] = "b" * 40
    result = shadow_consume_once(db, **info)
    assert "PINNED_RELEASE_UNVERIFIED" in result.blockers
    assert result.execution_authorized is False
    assert get_request(db, item.command_id) == item


def test_s14_stale_heartbeat_is_not_an_acknowledgement(scenario):
    db, item, info = scenario
    info["heartbeat"]["heartbeat_at_utc"] = (NOW - timedelta(seconds=121)).isoformat()
    result = shadow_consume_once(db, **info)
    assert "SUPERVISOR_HEARTBEAT_STALE" in result.blockers
    assert get_request(db, item.command_id) == item


def test_s15_shadow_observation_persists_identity_but_never_completes(scenario):
    db, item, info = scenario
    result = shadow_consume_once(db, **info)
    assert result.status == "OBSERVED_NOT_AUTHORIZED"
    assert result.observation_recorded is True
    assert result.execution_authorized is False
    receipt = get_request(db, item.command_id)
    assert receipt.state == "OBSERVED_NOT_AUTHORIZED"
    assert receipt.observer_generation == GENERATION
    assert receipt.observer_release_sha == SHA
    assert receipt.execution_authorized is False
    assert shadow_consume_once(db, **info).observation_recorded is False


def test_s16_missing_os_process_probe_fails_closed(scenario):
    db, item, info = scenario
    info.pop("process_fingerprint_fn")
    result = shadow_consume_once(db, **info)
    assert "PROCESS_START_FINGERPRINT_UNVERIFIED" in result.blockers
    assert get_request(db, item.command_id) == item


def test_s17_reused_pid_or_changed_os_fingerprint_rejected(scenario):
    db, item, info = scenario
    info["process_fingerprint_fn"] = lambda pid: "windows:999"
    result = shadow_consume_once(db, **info)
    assert "PROCESS_START_FINGERPRINT_UNVERIFIED" in result.blockers
    assert result.observation_recorded is False
    assert get_request(db, item.command_id) == item


def test_s18_independent_owner_recheck_required(scenario):
    db, item, info = scenario
    info["owner_verifier_fn"] = lambda: "UNVERIFIED"
    result = shadow_consume_once(db, **info)
    assert "OWNER_RECHECK_UNVERIFIED" in result.blockers
    assert get_request(db, item.command_id) == item


def test_s19_authority_rechecked_after_initial_snapshot(scenario):
    db, item, info = scenario
    info["controller_verifier_fn"] = lambda: {
        "mode": "candidate_mode",
        "candidate_execution_authorized": True,
    }
    result = shadow_consume_once(db, **info)
    assert "CONTROLLER_RECHECK_FAILED" in result.blockers
    assert get_request(db, item.command_id) == item


def test_s20_actual_release_head_fenced_to_pinned_sha(scenario):
    db, item, info = scenario
    info["release_head_fn"] = lambda _: "b" * 40
    result = shadow_consume_once(db, **info)
    assert "LIVE_RELEASE_HEAD_UNVERIFIED" in result.blockers
    assert get_request(db, item.command_id) == item


def test_shadow_consumer_rejects_missing_all_four_independent_callbacks(scenario):
    db, item, info = scenario
    for name in (
        "process_fingerprint_fn",
        "owner_verifier_fn",
        "controller_verifier_fn",
        "release_head_fn",
    ):
        info.pop(name)
    result = shadow_consume_once(db, **info)
    assert result.status == "OBSERVATION_BLOCKED"
    assert result.observation_recorded is False
    assert result.execution_authorized is False
    assert get_request(db, item.command_id) == item
