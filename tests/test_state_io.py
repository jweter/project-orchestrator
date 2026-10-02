import json

import pytest

from project_orchestrator.state_io import atomic_json


def test_atomic_json_never_rewrites_live_destination_in_place_on_replace_contention(tmp_path, monkeypatch):
    path = tmp_path / "authority.json"
    original = '{"generation":"old","payload":"intact"}\n'
    path.write_text(original, encoding="utf-8")

    attempts = 0
    real_replace = type(path).replace

    def blocked_replace(self, target):
        nonlocal attempts
        if self.name.startswith("authority.json.tmp."):
            attempts += 1
            raise PermissionError("destination busy")
        return real_replace(self, target)

    monkeypatch.setattr(type(path), "replace", blocked_replace)
    monkeypatch.setattr("project_orchestrator.state_io.time.sleep", lambda _seconds: None)

    with pytest.raises(PermissionError, match="destination busy"):
        atomic_json(path, {"generation": "new", "payload": "replacement"})

    assert attempts == 5
    assert path.read_text(encoding="utf-8") == original
    assert json.loads(path.read_text(encoding="utf-8"))["generation"] == "old"
    assert not list(tmp_path.glob("authority.json.tmp.*"))


def test_atomic_json_can_use_longer_bounded_replace_window_without_in_place_fallback(tmp_path, monkeypatch):
    path = tmp_path / "hands-off-grant-lifecycle.json"
    path.write_text('{"generation":"old"}\n', encoding="utf-8")
    attempts = 0
    real_replace = type(path).replace

    def transient_replace(self, target):
        nonlocal attempts
        if self.name.startswith("hands-off-grant-lifecycle.json.tmp."):
            attempts += 1
            if attempts < 8:
                raise PermissionError("transient destination busy")
        return real_replace(self, target)

    monkeypatch.setattr(type(path), "replace", transient_replace)
    monkeypatch.setattr("project_orchestrator.state_io.time.sleep", lambda _seconds: None)
    atomic_json(path, {"generation": "new"}, replace_attempts=20)
    assert attempts == 8
    assert json.loads(path.read_text(encoding="utf-8")) == {"generation": "new"}
    assert not list(tmp_path.glob("hands-off-grant-lifecycle.json.tmp.*"))
