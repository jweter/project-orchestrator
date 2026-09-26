import ctypes

import scripts.autonomous_slice_builder as builder


class FakeKernel32:
    def __init__(self, exit_code, creation_ticks=123456):
        self.exit_code=exit_code
        self.creation_ticks=creation_ticks
        self.times_called=False
        self.closed=False

    def OpenProcess(self, access, inherit, pid):
        return 99

    def GetExitCodeProcess(self, handle, exit_code):
        exit_code._obj.value=self.exit_code
        return 1

    def GetProcessTimes(self, handle, creation, exit_time, kernel, user):
        self.times_called=True
        creation._obj.dwLowDateTime=self.creation_ticks & 0xFFFFFFFF
        creation._obj.dwHighDateTime=(self.creation_ticks >> 32) & 0xFFFFFFFF
        return 1

    def CloseHandle(self, handle):
        self.closed=True
        return 1


def test_windows_fingerprint_rejects_terminated_process_object(monkeypatch):
    fake=FakeKernel32(exit_code=0xFFFFFFFF)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: fake, raising=False)
    assert builder._windows_process_start_fingerprint(25080) is None
    assert fake.times_called is False
    assert fake.closed is True


def test_windows_fingerprint_accepts_still_active_process(monkeypatch):
    fake=FakeKernel32(exit_code=259, creation_ticks=987654321)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: fake, raising=False)
    assert builder._windows_process_start_fingerprint(77) == "windows:987654321"
    assert fake.times_called is True
    assert fake.closed is True
