from __future__ import annotations

from pathlib import Path

import pytest

from project_orchestrator.kpi_runtime import (
    KpiRuntimeError,
    persistence_health,
    process_inbox_payload,
)


def event_payload(run_id: str = "run-1") -> dict:
    return {
        "schema_version": 1,
        "kind": "event",
        "event": {
            "observed_at": "2026-09-17T01:00:00Z",
            "run_id": run_id,
            "project": "rocksmith",
            "repository": "jweter/rocksmith-cdlc-generator",
            "entity_type": "pr",
            "entity_id": "697",
            "from_state": "GREEN",
            "to_state": "MERGED",
            "transition_class": "PROMOTION",
            "actor": "ORCHESTRATOR",
            "evidence_refs": ["https://github.com/jweter/rocksmith-cdlc-generator/pull/697"],
        },
    }


def finalize_payload(run_id: str = "run-1", generated_at: str = "2026-09-17T01:05:00Z") -> dict:
    return {
        "schema_version": 1,
        "kind": "finalize",
        "run_id": run_id,
        "generated_at": generated_at,
    }


def test_event_then_finalize_persists_and_health_is_green(tmp_path: Path):
    events = tmp_path / "events.jsonl"
    snapshots = tmp_path / "snapshots.jsonl"

    event_result = process_inbox_payload(
        event_payload(), event_ledger=events, snapshot_ledger=snapshots
    )
    assert event_result["status"] == "PERSISTED"
    assert event_result["event_ledger_count"] == 1

    final_result = process_inbox_payload(
        finalize_payload(), event_ledger=events, snapshot_ledger=snapshots
    )
    assert final_result == {
        "schema_version": 1,
        "kind": "finalize",
        "status": "PERSISTED",
        "run_id": "run-1",
        "run_event_count": 1,
        "snapshot_ledger_count": 1,
    }

    health = persistence_health(
        event_ledger=events, snapshot_ledger=snapshots, run_id="run-1"
    )
    assert health["status"] == "HEALTHY"
    assert health["snapshot_persisted"] is True
    assert health["run_event_count"] == 1


def test_repeated_identical_event_and_finalize_are_idempotent(tmp_path: Path):
    events = tmp_path / "events.jsonl"
    snapshots = tmp_path / "snapshots.jsonl"

    process_inbox_payload(event_payload(), event_ledger=events, snapshot_ledger=snapshots)
    process_inbox_payload(event_payload(), event_ledger=events, snapshot_ledger=snapshots)
    process_inbox_payload(finalize_payload(), event_ledger=events, snapshot_ledger=snapshots)
    process_inbox_payload(finalize_payload(), event_ledger=events, snapshot_ledger=snapshots)

    assert len(events.read_text(encoding="utf-8").splitlines()) == 1
    assert len(snapshots.read_text(encoding="utf-8").splitlines()) == 1


def test_conflicting_second_finalization_fails_closed(tmp_path: Path):
    events = tmp_path / "events.jsonl"
    snapshots = tmp_path / "snapshots.jsonl"

    process_inbox_payload(event_payload(), event_ledger=events, snapshot_ledger=snapshots)
    process_inbox_payload(finalize_payload(), event_ledger=events, snapshot_ledger=snapshots)

    with pytest.raises(KpiRuntimeError, match="conflicts with an existing durable snapshot"):
        process_inbox_payload(
            finalize_payload(generated_at="2026-09-17T01:06:00Z"),
            event_ledger=events,
            snapshot_ledger=snapshots,
        )


def test_health_fails_when_run_has_no_snapshot(tmp_path: Path):
    events = tmp_path / "events.jsonl"
    snapshots = tmp_path / "snapshots.jsonl"

    process_inbox_payload(event_payload(), event_ledger=events, snapshot_ledger=snapshots)
    health = persistence_health(
        event_ledger=events, snapshot_ledger=snapshots, run_id="run-1"
    )
    assert health["status"] == "FAILED"
    assert "exactly one durable run snapshot" in health["reason"]


def test_empty_ledgers_are_unknown_not_green(tmp_path: Path):
    health = persistence_health(
        event_ledger=tmp_path / "events.jsonl",
        snapshot_ledger=tmp_path / "snapshots.jsonl",
    )
    assert health["status"] == "UNKNOWN"


def test_payload_contract_rejects_unknown_fields(tmp_path: Path):
    raw = event_payload()
    raw["unexpected"] = True
    with pytest.raises(KpiRuntimeError, match="fields mismatch"):
        process_inbox_payload(
            raw,
            event_ledger=tmp_path / "events.jsonl",
            snapshot_ledger=tmp_path / "snapshots.jsonl",
        )
