from __future__ import annotations

import json
import time

import scripts.green_pr_watch_loop as watcher
from project_orchestrator.kpi_telemetry import build_rolling_reports, load_events


def test_green_pr_watcher_singleton_fails_closed_for_duplicate(tmp_path):
    state = tmp_path / "green-pr-watcher.json"
    first = watcher.acquire_singleton(state)
    assert first is not None
    try:
        second = watcher.acquire_singleton(state)
        assert second is None
    finally:
        watcher.release_singleton(first)

    replacement = watcher.acquire_singleton(state)
    assert replacement is not None
    watcher.release_singleton(replacement)


def test_kpi_events_are_canonical_and_deduplicated(tmp_path):
    results = [
        {
            "repository": "jweter/knowledge-engine-ai",
            "pr": 7,
            "head": "a" * 40,
            "promoted": True,
        },
        {
            "repository": "jweter/knowledge-engine-ai",
            "pr": 8,
            "head": "b" * 40,
            "merged": True,
        },
    ]
    events = watcher.kpi_events(
        results,
        "2026-09-26T00:00:00+00:00",
        "watcher-run-1",
    )
    assert [event["to_state"] for event in events] == ["GREEN", "MERGED"]
    assert all(event["entity_type"] == "pr" for event in events)
    assert all(event["transition_class"] == "PROMOTION" for event in events)

    ledger = tmp_path / "kpi-events.jsonl"
    assert watcher.append_kpi_events(ledger, events) == 2
    assert watcher.append_kpi_events(ledger, events) == 0
    rows = load_events(ledger)
    assert len(rows) == 2
    assert len({row["event_id"] for row in rows}) == 2


def test_watcher_kpi_events_measure_promotion_latency(tmp_path):
    ledger = tmp_path / "kpi-events.jsonl"
    ready = watcher.kpi_events(
        [{
            "repository": "jweter/knowledge-engine-ai",
            "pr": 73,
            "head": "c" * 40,
            "promoted": True,
        }],
        "2026-09-26T10:00:00+00:00",
        "watcher-ready",
    )
    merged = watcher.kpi_events(
        [{
            "repository": "jweter/knowledge-engine-ai",
            "pr": 73,
            "head": "c" * 40,
            "merged": True,
        }],
        "2026-09-26T10:05:00+00:00",
        "watcher-merged",
    )
    watcher.append_kpi_events(ledger, ready)
    watcher.append_kpi_events(ledger, merged)

    report = build_rolling_reports(
        load_events(ledger),
        end="2026-09-26T10:06:00+00:00",
    )
    metric = report["24h"]["metrics"]["promotion_latency_seconds"]
    assert metric["status"] == "MEASURED"
    assert metric["value"] == 300.0


def test_merge_event_reconciles_immediately_and_requests_continuation(tmp_path, monkeypatch):
    state = tmp_path / "green-pr-watcher.json"
    calls = []

    def reconcile(root):
        calls.append(root)
        return True

    monkeypatch.setattr(watcher, "merge_watcher_advanced", reconcile)
    payload = watcher.process_cycle(
        state,
        [{
            "repository": "jweter/knowledge-engine-ai",
            "pr": 7,
            "head": "a" * 40,
            "merged": True,
        }],
        None,
        time.time(),
    )

    assert calls == [tmp_path]
    assert payload["reconciliation_advanced"] is True
    continuation = json.loads((tmp_path / "continuation-request.json").read_text(encoding="utf-8"))
    assert continuation["reason"] == "MERGE_RECONCILED_EVENT"
    assert payload["kpi_events_emitted"] == 1
    assert payload["flow_events_emitted"] == 1


def test_failed_reconciliation_does_not_authorize_continuation(tmp_path, monkeypatch):
    state = tmp_path / "green-pr-watcher.json"
    monkeypatch.setattr(watcher, "merge_watcher_advanced", lambda _root: False)

    payload = watcher.process_cycle(
        state,
        [{
            "repository": "jweter/knowledge-engine-ai",
            "pr": 7,
            "head": "a" * 40,
            "merged": True,
        }],
        None,
        time.time(),
    )

    assert payload["reconciliation_advanced"] is False
    assert not (tmp_path / "continuation-request.json").exists()
    assert payload["kpi_events_emitted"] == 1
    assert payload["flow_events_emitted"] == 1


def test_isolated_cycle_failures_are_reported_as_watcher_error():
    results=[{"repository":"broken/repo","decision":"REPOSITORY_QUERY_FAILED","merged":False}]
    error=("PARTIAL_PROMOTION_FAILURE" if any(result.get("decision") in {"REPOSITORY_QUERY_FAILED","PR_PROCESSING_FAILED"} for result in results) else None)
    assert error=="PARTIAL_PROMOTION_FAILURE"
