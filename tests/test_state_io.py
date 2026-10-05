import json
import os
import shutil
import subprocess

import pytest

from project_orchestrator.state_io import atomic_json, optional_atomic_json


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
    monkeypatch.setattr("project_orchestrator.state_io._windows_replace_file", lambda *_args: False)
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


def test_optional_atomic_json_reports_contention_without_rewriting_destination(tmp_path, monkeypatch):
    path = tmp_path / "productive-liveness.json"
    original = '{"status":"old"}\n'
    path.write_text(original, encoding="utf-8")
    real_replace = type(path).replace

    def blocked_replace(self, target):
        if self.name.startswith("productive-liveness.json.tmp."):
            raise PermissionError(5, "Access is denied")
        return real_replace(self, target)

    monkeypatch.setattr(type(path), "replace", blocked_replace)
    monkeypatch.setattr("project_orchestrator.state_io._windows_replace_file", lambda *_args: False)
    monkeypatch.setattr("project_orchestrator.state_io.time.sleep", lambda _seconds: None)
    assert optional_atomic_json(path, {"status": "new"}, replace_attempts=3) is False
    assert path.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob("productive-liveness.json.tmp.*"))


def test_optional_atomic_json_reports_success(tmp_path):
    path = tmp_path / "productive-liveness.json"
    assert optional_atomic_json(path, {"status": "fresh"}) is True
    assert json.loads(path.read_text(encoding="utf-8")) == {"status": "fresh"}


def test_atomic_json_uses_windows_fallback_after_bounded_replace_contention(tmp_path, monkeypatch):
    path = tmp_path / "supervisor.json"
    path.write_text('{"generation":"old"}\n', encoding="utf-8")
    real_replace = type(path).replace
    calls = []

    def blocked_replace(self, target):
        if self.name.startswith("supervisor.json.tmp."):
            raise PermissionError(5, "Access is denied")
        return real_replace(self, target)

    def successful_fallback(replacement, destination):
        calls.append((replacement.name, destination.name))
        destination.write_text(replacement.read_text(encoding="utf-8"), encoding="utf-8")
        replacement.unlink()
        return True

    monkeypatch.setattr(type(path), "replace", blocked_replace)
    monkeypatch.setattr("project_orchestrator.state_io._windows_replace_file", successful_fallback)
    monkeypatch.setattr("project_orchestrator.state_io.time.sleep", lambda _seconds: None)

    atomic_json(path, {"generation": "new"}, replace_attempts=3)
    assert calls and calls[0][1] == "supervisor.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"generation": "new"}
    assert not list(tmp_path.glob("supervisor.json.tmp.*"))


@pytest.mark.skipif(os.name != "nt" or shutil.which("node") is None, reason="Windows + Node integration")
def test_atomic_json_windows_replacefile_handles_node_reader(tmp_path):
    path = tmp_path / "supervisor.json"
    path.write_text('{"generation":"old"}\n', encoding="utf-8")
    script = (
        "const fs=require('fs');"
        "const fd=fs.openSync(process.argv[1],'r');"
        "console.log('READY');"
        "setTimeout(()=>{fs.closeSync(fd);process.exit(0)},10000);"
    )
    child = subprocess.Popen(
        [shutil.which("node"), "-e", script, str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "READY"
        atomic_json(path, {"generation": "new"}, replace_attempts=1)
        assert json.loads(path.read_text(encoding="utf-8")) == {"generation": "new"}
    finally:
        child.terminate()
        child.wait(timeout=5)


def test_windows_replacefile_1176_preserves_original_destination(tmp_path, monkeypatch):
    import project_orchestrator.state_io as state_io

    destination = tmp_path / "supervisor.json"
    replacement = tmp_path / "supervisor.json.tmp"
    destination.write_text('{"generation":"old"}\n', encoding="utf-8")
    replacement.write_text('{"generation":"new"}\n', encoding="utf-8")
    observed = {}

    def fail_1176(dest, repl, backup):
        observed["backup"] = backup
        assert dest == destination
        assert repl == replacement
        return False, state_io.ERROR_UNABLE_TO_MOVE_REPLACEMENT

    monkeypatch.setattr(state_io, "_call_windows_replace_file", fail_1176)
    assert state_io._windows_replace_file(replacement, destination) is False
    assert json.loads(destination.read_text(encoding="utf-8")) == {"generation": "old"}
    assert replacement.exists()
    assert observed["backup"] != destination
    assert observed["backup"].parent == destination.parent


def test_windows_replacefile_1177_restores_backup_to_canonical_path(tmp_path, monkeypatch):
    import project_orchestrator.state_io as state_io

    destination = tmp_path / "supervisor.json"
    replacement = tmp_path / "supervisor.json.tmp"
    destination.write_text('{"generation":"old"}\n', encoding="utf-8")
    replacement.write_text('{"generation":"new"}\n', encoding="utf-8")

    def fail_1177(dest, repl, backup):
        dest.replace(backup)
        assert not dest.exists()
        assert repl.exists()
        assert backup.exists()
        return False, state_io.ERROR_UNABLE_TO_MOVE_REPLACEMENT_2

    monkeypatch.setattr(state_io, "_call_windows_replace_file", fail_1177)
    assert state_io._windows_replace_file(replacement, destination) is False
    assert json.loads(destination.read_text(encoding="utf-8")) == {"generation": "old"}
    assert replacement.exists()
    assert not list(tmp_path.glob("supervisor.json.backup.*"))


def test_windows_replacefile_success_removes_old_backup(tmp_path, monkeypatch):
    import project_orchestrator.state_io as state_io

    destination = tmp_path / "supervisor.json"
    replacement = tmp_path / "supervisor.json.tmp"
    destination.write_text('{"generation":"old"}\n', encoding="utf-8")
    replacement.write_text('{"generation":"new"}\n', encoding="utf-8")

    def succeed(dest, repl, backup):
        dest.replace(backup)
        repl.replace(dest)
        return True, 0

    monkeypatch.setattr(state_io, "_call_windows_replace_file", succeed)
    assert state_io._windows_replace_file(replacement, destination) is True
    assert json.loads(destination.read_text(encoding="utf-8")) == {"generation": "new"}
    assert not list(tmp_path.glob("supervisor.json.backup.*"))
