"""Regression: Windows PID liveness must never terminate active builders."""

import ctypes
import socket

from project_orchestrator import recovery_manager as recovery


class FakeKernel32:
    def __init__(self, *, handle=42, exit_code=259, query_ok=True):
        self.handle = handle
        self.exit_code = exit_code
        self.query_ok = query_ok
        self.closed = False

    def OpenProcess(self, access, inherit, pid):
        assert access == 0x1000  # Query-only; no PROCESS_TERMINATE rights.
        assert inherit is False
        assert pid == 1234
        return self.handle

    def GetExitCodeProcess(self, handle, exit_code):
        assert handle == self.handle
        exit_code._obj.value = self.exit_code
        return self.query_ok

    def CloseHandle(self, handle):
        assert handle == self.handle
        self.closed = True


def test_windows_process_probe_reports_live_and_dead_without_signaling(monkeypatch):
    for code, expected in [(259, "ALIVE"), (0, "DEAD")]:
        fake = FakeKernel32(exit_code=code)
        monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: fake, raising=False)
        assert recovery._windows_pid_liveness(1234) == expected
        assert fake.closed


def test_windows_process_probe_fails_closed_on_unreadable_handle(monkeypatch):
    fake = FakeKernel32(query_ok=False)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: fake, raising=False)
    assert recovery._windows_pid_liveness(1234) == "UNKNOWN"
    assert fake.closed


def test_windows_process_probe_distinguishes_missing_from_denied(monkeypatch):
    fake = FakeKernel32(handle=0)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: fake, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 87, raising=False)
    assert recovery._windows_pid_liveness(1234) == "DEAD"
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    assert recovery._windows_pid_liveness(1234) == "UNKNOWN"


def test_windows_worker_liveness_preserves_fingerprint_guard(monkeypatch):
    import scripts.autonomous_slice_builder as builder

    monkeypatch.setattr(recovery.sys, "platform", "win32")
    monkeypatch.setattr(recovery, "_windows_pid_liveness", lambda pid: "ALIVE")
    monkeypatch.setattr(builder, "process_start_fingerprint", lambda pid: "windows:current")
    monkeypatch.setattr(recovery.os, "kill", lambda *args: (_ for _ in ()).throw(AssertionError("destructive probe")))
    worker = {"host": socket.gethostname(), "pid": 1234, "process_start_fingerprint": "windows:current"}
    assert recovery.worker_liveness(worker) == "ALIVE"
    worker["process_start_fingerprint"] = "windows:previous"
    assert recovery.worker_liveness(worker) == "DEAD"
