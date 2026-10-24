"""Only a terminal verified outcome may be counted as delivered code."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from project_orchestrator.delivery_throughput import assess_delivery_throughput


NOW = datetime(2026, 10, 24, 23, 0, tzinfo=UTC)
START = NOW - timedelta(minutes=44)


def record(tmp_path, grants, started=START):
    (tmp_path / "supervisor-process-instance.json").write_text(
        json.dumps(
            {
                "started_at_utc": started.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "hands-off-grant-lifecycle.json").write_text(
        json.dumps(
            {
                "grants": grants,
            }
        ),
        encoding="utf-8",
    )


def assess(tmp_path):
    return assess_delivery_throughput(
        tmp_path,
        ["iron-god"],
        {"executing_total": 1},
        now=NOW,
    )


def test_running_workers_without_delivery_are_a_stall(tmp_path):
    record(
        tmp_path,
        {
            "a": {
                "state": "RUNNING",
                "last_heartbeat_at": NOW.isoformat(),
            }
        },
    )
    result = assess(tmp_path)
    assert result["status"] == "VERIFIED_DELIVERY_STALLED"
    assert result["verified_deliveries"] == 0
    assert result["current_execution_workers"] == 1
    assert result["execution_authorized"] is False


def test_committed_candidate_head_is_not_a_verified_merge(tmp_path):
    record(
        tmp_path,
        {
            "a": {
                "state": "COMPLETED",
                "exact_head": "a" * 40,
                "head_sha": "b" * 40,
                "finished_at": NOW.isoformat(),
                "result_status": "PR_OPEN",
            }
        },
    )
    result = assess(tmp_path)
    assert result["candidate_heads"] == 1
    assert result["verified_deliveries"] == 0
    assert result["status"] == "VERIFIED_DELIVERY_STALLED"


def test_verified_merge_counts_only_in_current_incarnation(tmp_path):
    record(
        tmp_path,
        {
            "old": {
                "state": "COMPLETED",
                "outcome_quality": "MERGED_VERIFIED",
                "finished_at": (START - timedelta(seconds=5)).isoformat(),
            },
            "nope": {
                "state": "FAILED",
                "outcome_quality": "MERGED_VERIFIED",
                "finished_at": NOW.isoformat(),
            },
            "current": {
                "state": "COMPLETED",
                "outcome_quality": "MERGED_VERIFIED",
                "finished_at": NOW.isoformat(),
            },
            "reality": {
                "state": "COMPLETED",
                "outcome_quality": "PRODUCT_REALITY_VERIFIED",
                "finished_at": NOW.isoformat(),
            },
        },
    )
    result = assess(tmp_path)
    assert result["status"] == "VERIFIED_DELIVERY"
    assert result["verified_deliveries"] == 2
    assert result["merged_verified"] == 2
    assert result["product_reality_verified"] == 1
    assert result["verified_grant_ids"] == ["current", "reality"]


def test_recent_engine_starts_do_not_immediately_count_as_failed(tmp_path):
    record(tmp_path, {}, started=NOW - timedelta(minutes=5))
    assert assess(tmp_path)["status"] == "AWAITING_FIRST_VERIFIED_DELIVERY"


def test_unknown_owner_generation_cannot_claim_a_verified_run(tmp_path):
    (tmp_path / "hands-off-grant-lifecycle.json").write_text(
        json.dumps(
            {
                "grants": {
                    "old": {
                        "state": "COMPLETED",
                        "outcome_quality": "MERGED_VERIFIED",
                        "finished_at": NOW.isoformat(),
                    }
                }
            }
        )
    )
    assert assess(tmp_path)["status"] == "START_TIME_UNVERIFIED"
    assert assess(tmp_path)["verified_deliveries"] == 0


def test_authority_absence_and_bad_timestamp_fail_closed(tmp_path):
    record(tmp_path, {})
    summary = assess_delivery_throughput(
        tmp_path,
        [],
        {},
        now=NOW,
    )
    assert summary["status"] == "NO_AUTHORIZED_WORK"
    assert summary["current_execution_workers"] == 0
