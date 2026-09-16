from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_orchestrator.kpi_telemetry import (
    KpiTelemetryError,
    append_event,
    append_snapshot,
    build_flow_report,
    build_rolling_reports,
    build_run_snapshot,
    load_events,
    load_snapshots,
    validate_event,
)


def event(
    at: str,
    *,
    entity_type: str,
    entity_id: str,
    from_state: str | None,
    to_state: str,
    transition_class: str,
    actor: str = "ORCHESTRATOR",
    project: str = "knowledge-engine",
    repository: str = "jweter/knowledge-engine-ai",
    run_id: str = "run-1",
    ref: str | None = None,
):
    return {
        "observed_at": at,
        "run_id": run_id,
        "project": project,
        "repository": repository,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "from_state": from_state,
        "to_state": to_state,
        "transition_class": transition_class,
        "actor": actor,
        "evidence_refs": [ref or f"{repository}:{entity_type}:{entity_id}:{to_state}:{at}"],
    }


def sample_events():
    return [
        event("2026-09-15T10:00:00Z", entity_type="issue", entity_id="129", from_state="OPEN", to_state="IN_PROGRESS", transition_class="ADVANCE"),
        event("2026-09-15T10:05:00Z", entity_type="issue", entity_id="84", from_state="OPEN", to_state="BLOCKED", transition_class="REGRESSION"),
        event("2026-09-15T10:10:00Z", entity_type="pr", entity_id="73", from_state=None, to_state="OPEN", transition_class="NEUTRAL"),
        event("2026-09-15T10:40:00Z", entity_type="pr", entity_id="73", from_state="OPEN", to_state="PREFLIGHT_GREEN", transition_class="PROMOTION"),
        event("2026-09-15T11:10:00Z", entity_type="pr", entity_id="73", from_state="PREFLIGHT_GREEN", to_state="MERGED", transition_class="PROMOTION"),
        event("2026-09-15T10:20:00Z", entity_type="ci", entity_id="73:preflight", from_state="PENDING", to_state="FAILED", transition_class="FAILURE"),
        event("2026-09-15T10:30:00Z", entity_type="ci", entity_id="73:preflight", from_state="FAILED", to_state="SUCCESS", transition_class="REPAIR"),
        event("2026-09-15T10:22:00Z", entity_type="ci", entity_id="73:unit", from_state="PENDING", to_state="SUCCESS", transition_class="PROMOTION"),
        event("2026-09-15T10:00:00Z", entity_type="branch", entity_id="feature/a", from_state=None, to_state="PRESENT", transition_class="NEUTRAL"),
        event("2026-09-15T10:01:00Z", entity_type="branch", entity_id="feature/b", from_state=None, to_state="PRESENT", transition_class="NEUTRAL"),
        event("2026-09-15T10:50:00Z", entity_type="branch", entity_id="feature/a", from_state="PRESENT", to_state="DELETED", transition_class="HYGIENE"),
        event("2026-09-15T10:55:00Z", entity_type="branch", entity_id="feature/b", from_state="PRESENT", to_state="STALE", transition_class="HYGIENE"),
        event("2026-09-15T11:00:00Z", entity_type="issue", entity_id="200", from_state="BLOCKED", to_state="IN_PROGRESS", transition_class="HUMAN_INTERVENTION", actor="HUMAN"),
        event(
            "2026-09-15T11:05:00Z",
            entity_type="pr",
            entity_id="99",
            from_state="OPEN",
            to_state="MERGED",
            transition_class="PROMOTION",
            actor="EXTERNAL_AUTOMATION",
            project="portfolio",
            repository="jweter/project-orchestrator",
        ),
    ]


def test_event_id_is_content_addressed_and_mismatch_fails():
    raw = event("2026-09-15T10:00:00Z", entity_type="issue", entity_id="1", from_state="OPEN", to_state="IN_PROGRESS", transition_class="ADVANCE")
    normalized = validate_event(raw)
    assert len(normalized["event_id"]) == 24
    with pytest.raises(KpiTelemetryError, match="event_id does not match"):
        validate_event({**raw, "event_id": "wrong"})


def test_append_only_ledgers_are_idempotent(tmp_path: Path):
    ledger = tmp_path / "events.jsonl"
    raw = event("2026-09-15T10:00:00Z", entity_type="issue", entity_id="1", from_state="OPEN", to_state="IN_PROGRESS", transition_class="ADVANCE")
    append_event(ledger, raw)
    append_event(ledger, raw)
    assert len(load_events(ledger)) == 1
    assert len([line for line in ledger.read_text().splitlines() if line.strip()]) == 1


def test_flow_report_calculates_requested_metrics_without_control_plane_pollution():
    report = build_flow_report(
        sample_events(),
        start="2026-09-15T09:00:00Z",
        end="2026-09-15T12:00:00Z",
    )
    metrics = report["metrics"]

    assert metrics["issue_advancement_rate"]["value"] == pytest.approx(1 / 3)
    assert metrics["issue_advancement_rate"]["advanced_issues"] == 1
    assert metrics["cycle_time_seconds"]["value"] == 3600
    assert metrics["promotion_latency_seconds"]["value"] == 1800
    assert metrics["repair_latency_seconds"]["value"] == 600
    assert metrics["first_pass_ci_rate"]["value"] == pytest.approx(0.5)
    assert metrics["branch_net_delta"]["value"] == 1
    assert metrics["branch_net_delta"]["created"] == 2
    assert metrics["branch_net_delta"]["deleted"] == 1
    assert metrics["branch_hygiene_debt"]["value"] == 1
    assert metrics["human_intervention_rate"]["human_intervention_count"] == 1
    assert report["event_count"] == len(sample_events()) - 1


def test_missing_pairs_stay_unknown_instead_of_becoming_zero():
    only_open = [
        event("2026-09-15T10:00:00Z", entity_type="pr", entity_id="1", from_state=None, to_state="OPEN", transition_class="NEUTRAL")
    ]
    report = build_flow_report(only_open, start="2026-09-15T09:00:00Z", end="2026-09-15T11:00:00Z")
    assert report["metrics"]["cycle_time_seconds"]["status"] == "UNKNOWN"
    assert report["metrics"]["first_pass_ci_rate"]["status"] == "UNKNOWN"
    assert report["metrics"]["branch_net_delta"]["status"] == "UNKNOWN"


def test_rolling_reports_have_24h_7d_30d_and_respect_window_boundaries():
    events = sample_events() + [
        event("2026-09-10T10:00:00Z", entity_type="issue", entity_id="old", from_state="OPEN", to_state="IN_PROGRESS", transition_class="ADVANCE", run_id="run-old")
    ]
    reports = build_rolling_reports(events, end="2026-09-15T12:00:00Z")
    assert set(reports) == {"24h", "7d", "30d"}
    assert reports["24h"]["event_count"] == len(sample_events()) - 1
    assert reports["7d"]["event_count"] == len(sample_events())
    assert reports["30d"]["event_count"] == len(sample_events())


def test_run_snapshot_is_append_only_and_contains_rolling_reports(tmp_path: Path):
    events = sample_events()
    snapshot = build_run_snapshot(events, run_id="run-1", generated_at="2026-09-15T12:00:00Z")
    assert snapshot["run_event_count"] == len(events)
    assert set(snapshot["rolling_reports"]) == {"24h", "7d", "30d"}

    path = tmp_path / "snapshots.jsonl"
    append_snapshot(path, snapshot)
    append_snapshot(path, snapshot)
    loaded = load_snapshots(path)
    assert len(loaded) == 1
    assert loaded[0]["run_id"] == "run-1"


def test_snapshot_ledger_rejects_duplicate_run_id_with_conflicting_content(tmp_path: Path):
    path = tmp_path / "snapshots.jsonl"
    first = build_run_snapshot(sample_events(), run_id="run-1", generated_at="2026-09-15T12:00:00Z")
    append_snapshot(path, first)
    conflicting = json.loads(json.dumps(first))
    conflicting["run_event_count"] += 1
    returned = append_snapshot(path, conflicting)
    assert returned == first
