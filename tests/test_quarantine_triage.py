"""Quarantine reporting must never assume an unknown lease owner is dead."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from project_orchestrator.quarantine_triage import triage_quarantines


NOW = datetime(2026, 10, 24, 23, tzinfo=UTC)


def test_historical_and_recent_grants_are_not_conflated():
    records = {
        "old": {
            "result_status": "BUILDER_EXITED_WITHOUT_CLAIM:1",
            "recovery_result": "RECOVERY_RETRY_EXHAUSTED",
            "finished_at": (NOW - timedelta(days=28)).isoformat(),
        },
        "recent": {
            "result_status": "CLAIM_LEASE_EXPIRED_WORKER_DEAD",
            "recovery_result": "RECOVERY_EARLY_STAGE_DIRTY_PRESERVED",
            "finished_at": NOW.isoformat(),
            "worker_pid": 199,
            "process_start_fingerprint": "windows:123",
        },
        "unknown": {"result_status": "SOMETHING_NEW"},
    }
    before = repr(records)
    result = triage_quarantines(records, now=NOW)
    assert result["count"] == 3
    assert result["historical_count"] == 1
    assert result["recent_count"] == 1
    assert result["age_unverified_count"] == 1
    assert result["owner_identity_recorded_count"] == 1
    assert result["preserved_work_count"] == 1
    assert result["disposition"] == "REVIEW_INDIVIDUALLY_DO_NOT_BULK_RELEASE"
    assert result["authority_mutated"] is False
    assert repr(records) == before


def test_empty_quarantine_has_no_unverified_recovery_conclusion():
    result = triage_quarantines({}, now=NOW)
    assert result["count"] == 0
    assert result["disposition"] == "NO_QUARANTINES"
    assert result["recent_count"] == 0
    assert result["authority_mutated"] is False
