import json

import pytest

from project_orchestrator.actor_metrics import read_events, summarize


def test_summary_counts_real_actor_states(tmp_path):
    path = tmp_path / "actor.jsonl"
    rows = [
        {"timestamp_utc": "2026-09-18T01:00:00+00:00", "state": "IDLE_AVAILABLE"},
        {"timestamp_utc": "2026-09-18T01:01:00+00:00", "state": "EXECUTING"},
        {"timestamp_utc": "2026-09-18T01:01:02+00:00", "state": "IDLE_AVAILABLE", "result": "bridge_cycle_green"},
    ]
    path.write_text("\n".join(json.dumps(x) for x in rows) + "\n")
    result = summarize(read_events(path))
    assert result["green_bridge_cycles"] == 1
    assert result["state_counts"]["EXECUTING"] == 1
    assert result["latest_state"] == "IDLE_AVAILABLE"


def test_cooldown_preserves_measured_backoff():
    result = summarize([
        {"timestamp_utc": "2026-09-18T01:00:00+00:00", "state": "COOLDOWN", "backoff_seconds": 120}
    ])
    assert result["cooldown_count"] == 1
    assert result["max_observed_backoff_seconds"] == 120.0


def test_unknown_state_fails_closed(tmp_path):
    path = tmp_path / "actor.jsonl"
    path.write_text('{"timestamp_utc":"2026-09-18T01:00:00+00:00","state":"MAGIC"}\n')
    with pytest.raises(ValueError, match="invalid actor telemetry state"):
        read_events(path)


def test_missing_file_is_offline(tmp_path):
    assert summarize(read_events(tmp_path / "missing"))["latest_state"] == "OFFLINE"
