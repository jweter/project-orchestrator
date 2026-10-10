import json

import pytest

from project_orchestrator import state_io


def test_atomic_json_writes_valid_durable_image(tmp_path):
    path = tmp_path / "hands-off-grant-lifecycle.json"
    state_io.atomic_json(path, {"schema_version": 1, "grants": {"a": {"state": "CLAIMED"}}})
    assert json.loads(path.read_text(encoding="utf-8"))["grants"]["a"]["state"] == "CLAIMED"
    assert list(tmp_path.glob("*.tmp.*")) == []


def test_failed_fsync_preserves_original_and_cleans_staging(tmp_path, monkeypatch):
    path = tmp_path / "hands-off-grant-lifecycle.json"
    path.write_text('{"grants":{}}', encoding="utf-8")

    def fail_fsync(_fd):
        raise OSError("simulated storage failure")

    monkeypatch.setattr(state_io.os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="simulated storage failure"):
        state_io.atomic_json(path, {"grants": {"unsafe": {"state": "ISSUED"}}})
    assert json.loads(path.read_text(encoding="utf-8")) == {"grants": {}}
    assert list(tmp_path.glob("*.tmp.*")) == []
