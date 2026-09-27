from scripts.green_pr_watch_loop import acquire_singleton, release_singleton


def test_green_pr_watcher_singleton_fails_closed_for_duplicate(tmp_path):
    state = tmp_path / "green-pr-watcher.json"
    first = acquire_singleton(state)
    assert first is not None
    try:
        second = acquire_singleton(state)
        assert second is None
    finally:
        release_singleton(first)

    replacement = acquire_singleton(state)
    assert replacement is not None
    release_singleton(replacement)


import json
import time

import scripts.green_pr_watch_loop as watcher


def test_lifecycle_events_are_stable_and_deduplicated(tmp_path):
    state = tmp_path / "green-pr-watcher.json"
    results = [
        {"repository": "o/r", "pr": 7, "head": "abc", "promoted": True},
        {"repository": "o/r", "pr": 8, "head": "def", "merged": True},
    ]
    events = watcher.lifecycle_events(results, "2026-09-26T00:00:00+00:00")
    assert [event["event_type"] for event in events] == ["PR_READY", "PR_MERGED"]

    ledger = tmp_path / "flow-events.jsonl"
    assert watcher.append_events(ledger, events) == 2
    assert watcher.append_events(ledger, events) == 0
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert len({row["event_id"] for row in rows}) == 2


def test_merge_event_reconciles_immediately_and_requests_continuation(tmp_path, monkeypatch):
    state = tmp_path / "green-pr-watcher.json"
    calls = []

    def reconcile(root):
        calls.append(root)
        return True

    monkeypatch.setattr(watcher, "merge_watcher_advanced", reconcile)
    payload = watcher.process_cycle(
        state,
        [{"repository": "o/r", "pr": 7, "head": "abc", "merged": True}],
        None,
        time.time(),
    )

    assert calls == [tmp_path]
    assert payload["reconciliation_advanced"] is True
    continuation = json.loads((tmp_path / "continuation-request.json").read_text(encoding="utf-8"))
    assert continuation["reason"] == "MERGE_RECONCILED_EVENT"
    assert payload["flow_events_emitted"] == 1


def test_failed_reconciliation_does_not_authorize_continuation(tmp_path, monkeypatch):
    state = tmp_path / "green-pr-watcher.json"
    monkeypatch.setattr(watcher, "merge_watcher_advanced", lambda _root: False)

    payload = watcher.process_cycle(
        state,
        [{"repository": "o/r", "pr": 7, "head": "abc", "merged": True}],
        None,
        time.time(),
    )

    assert payload["reconciliation_advanced"] is False
    assert not (tmp_path / "continuation-request.json").exists()
    assert payload["flow_events_emitted"] == 1
