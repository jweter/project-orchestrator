"""Corrupt persisted dispatch state must never silently become an empty queue."""

import json

import pytest

from project_orchestrator import iron_god_backlog as backlog


def test_missing_state_file_starts_empty(tmp_path):
    assert backlog.load_state(tmp_path) == {"schema_version": 1, "items": {}}


@pytest.mark.parametrize(
    "content",
    ["{truncated", "[]", '{"schema_version": 1, "items": []}', '{"schema_version": 1}'],
)
def test_existing_malformed_state_fails_closed(tmp_path, content):
    (tmp_path / "backlog-state.json").write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="BACKLOG_STATE_INVALID"):
        backlog.load_state(tmp_path)


def test_record_item_cannot_overwrite_corrupt_dispatch_history(tmp_path):
    path = tmp_path / "backlog-state.json"
    path.write_text("{truncated", encoding="utf-8")
    with pytest.raises(ValueError, match="BACKLOG_STATE_INVALID"):
        backlog.record_item(tmp_path, "S1", packet_id="packet-1", outcome="DISPATCHED")
    assert path.read_text(encoding="utf-8") == "{truncated"


def test_existing_valid_dispatch_state_is_preserved(tmp_path):
    state = {
        "schema_version": 1,
        "items": {"S1": {"attempts": 1, "packets": ["p1"], "outcome": "DISPATCHED"}},
    }
    (tmp_path / "backlog-state.json").write_text(json.dumps(state), encoding="utf-8")
    assert backlog.load_state(tmp_path) == state
