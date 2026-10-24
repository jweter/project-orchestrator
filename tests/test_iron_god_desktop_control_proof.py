"""Regression proof: a desktop success toast is not a controller acknowledgment."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from project_orchestrator.iron_god_desktop_control_proof import diagnose_desktop_resume

NOW = datetime(2026, 10, 24, 14, 0, tzinfo=UTC)
SHA = "a" * 40
GENERATION = "generation-1"
COMMAND_ID = "request-00000000000001"


def _at(seconds_ago: int) -> str:
    return (NOW - timedelta(seconds=seconds_ago)).isoformat()


def evidence() -> dict:
    return {
        "request": {
            "command": "TAKE_THE_WHEEL",
            "command_id": COMMAND_ID,
            "requested_at_utc": _at(20),
        },
        "acknowledgement": {
            "command_id": COMMAND_ID,
            "acknowledged_by_engine": True,
            "status": "APPLIED",
            "accepted_sha": SHA,
            "process_instance_id": GENERATION,
            "acknowledged_at_utc": _at(10),
        },
        "supervisor_pin": {
            "enabled": True,
            "accepted_sha": SHA,
            "accepted_repo": "C:/Engine/Release",
        },
        "actor_pin": {
            "enabled": True,
            "accepted_sha": SHA,
            "accepted_repo": "C:\\Engine\\Release",
            "actor_repo": "C:/Engine/Actor",
        },
        "heartbeat": {
            "process_instance_id": GENERATION,
            "status": "RUNNING_CURRENT",
            "actor_revision": SHA,
            "heartbeat_at_utc": _at(5),
        },
        "controller": {
            "mode": "candidate_mode",
            "candidate_execution_authorized": True,
        },
        "supervisor_processes": [
            {
                "pid": 345,
                "process_instance_id": GENERATION,
                "accepted_sha": SHA,
                "release_repo": "C:/Engine/Release",
            }
        ],
        "actor_processes": [
            {"pid": 789, "accepted_sha": SHA, "actor_repo": "C:/Engine/Actor"}
        ],
        "owner_authority": "VERIFIED_RELEASED",
        "now": NOW,
    }


def test_matching_current_evidence_is_never_execution_authority():
    result = diagnose_desktop_resume(**evidence())
    assert result.status == "EVIDENCE_ALIGNED_NOT_AUTHORIZATION"
    assert result.problems == ()
    assert result.execution_authorized is False


def test_legacy_success_receipt_without_command_ack_is_rejected():
    payload = evidence()
    payload["acknowledgement"] = {
        "verified": True,
        "checked_at": _at(1),
        "accepted_sha": SHA,
    }
    result = diagnose_desktop_resume(**payload)
    assert result.status == "BLOCKED"
    assert "SUPERVISOR_COMMAND_ACK_MISSING" in result.problems
    assert "ACK_GENERATION_MISMATCH" in result.problems
    assert result.execution_authorized is False


def test_reused_ack_different_click_is_rejected():
    payload = evidence()
    payload["acknowledgement"]["command_id"] = "different-command-id"
    result = diagnose_desktop_resume(**payload)
    assert "SUPERVISOR_COMMAND_ACK_MISSING" in result.problems


def test_owner_control_is_independent_of_resume_receipt():
    payload = evidence()
    payload["owner_authority"] = "UNVERIFIED"
    assert "OWNER_CONTROL_UNVERIFIED" in diagnose_desktop_resume(**payload).problems


def test_shadow_controller_is_not_promoted_by_a_good_heartbeat():
    payload = evidence()
    payload["controller"] = {
        "mode": "shadow_state_only",
        "candidate_execution_authorized": False,
    }
    result = diagnose_desktop_resume(**payload)
    assert "CONTROLLER_CANDIDATE_EXECUTION_DISABLED" in result.problems
    assert result.execution_authorized is False


def test_stale_or_future_heartbeat_cannot_verify_runtime():
    payload = evidence()
    payload["heartbeat"]["heartbeat_at_utc"] = _at(200)
    assert (
        "SUPERVISOR_HEARTBEAT_NOT_FRESH" in diagnose_desktop_resume(**payload).problems
    )
    payload["heartbeat"]["heartbeat_at_utc"] = _at(-1)
    assert (
        "SUPERVISOR_HEARTBEAT_NOT_FRESH" in diagnose_desktop_resume(**payload).problems
    )


def test_historical_click_receipt_is_not_current_acknowledgment():
    payload = evidence()
    payload["request"]["requested_at_utc"] = _at(400)
    payload["acknowledgement"]["acknowledged_at_utc"] = _at(390)
    result = diagnose_desktop_resume(**payload)
    assert "COMMAND_REQUEST_NOT_FRESH" in result.problems
    assert "SUPERVISOR_ACK_NOT_FRESH" in result.problems


def test_ack_predating_request_is_rejected():
    payload = evidence()
    payload["acknowledgement"]["acknowledged_at_utc"] = _at(30)
    assert "ACK_PREDATES_REQUEST" in diagnose_desktop_resume(**payload).problems


def test_mismatched_release_pins_are_not_accepted():
    payload = evidence()
    payload["actor_pin"]["accepted_sha"] = "b" * 40
    assert (
        "SUPERVISOR_ACTOR_PIN_MISMATCH" in diagnose_desktop_resume(**payload).problems
    )


def test_process_absence_and_duplicate_supervisors_fail_closed():
    payload = evidence()
    payload["supervisor_processes"] = []
    payload["actor_processes"] = []
    result = diagnose_desktop_resume(**payload)
    assert "SUPERVISOR_PROCESS_COUNT_UNVERIFIED" in result.problems
    assert "ACTOR_PROCESS_COUNT_UNVERIFIED" in result.problems

    payload = evidence()
    payload["supervisor_processes"].append(deepcopy(payload["supervisor_processes"][0]))
    assert (
        "SUPERVISOR_PROCESS_COUNT_UNVERIFIED"
        in diagnose_desktop_resume(**payload).problems
    )


def test_wrong_generation_not_hidden_by_current_release():
    payload = evidence()
    payload["acknowledgement"]["process_instance_id"] = "old-generation"
    payload["supervisor_processes"][0]["process_instance_id"] = "old-generation"
    result = diagnose_desktop_resume(**payload)
    assert "ACK_GENERATION_MISMATCH" in result.problems
    assert "SUPERVISOR_PROCESS_IDENTITY_MISMATCH" in result.problems


def test_invalid_or_naive_times_fail_closed_without_mutation():
    payload = evidence()
    before = deepcopy(payload)
    payload["request"]["requested_at_utc"] = "2026-10-24T14:00:00"
    assert "COMMAND_REQUEST_NOT_FRESH" in diagnose_desktop_resume(**payload).problems
    payload["request"]["requested_at_utc"] = "invalid"
    assert "COMMAND_REQUEST_NOT_FRESH" in diagnose_desktop_resume(**payload).problems
    assert before["controller"] == payload["controller"]


def test_no_clock_skew_or_negative_freshness_horizon():
    payload = evidence()
    payload["now"] = NOW.replace(tzinfo=None)
    with pytest.raises(ValueError, match="aware clock"):
        diagnose_desktop_resume(**payload)
    payload["now"] = NOW
    payload["max_age_seconds"] = 0
    with pytest.raises(ValueError, match="freshness horizon"):
        diagnose_desktop_resume(**payload)
