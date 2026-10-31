"""Windows Job Object fence for *owned, newly created* supervised processes.

This helper never enumerates or terminates unrelated machine processes.
A job with KILL_ON_JOB_CLOSE keeps descendant processes bound to their
original supervisor even after their immediate parent exits.
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


class ProcessJobError(RuntimeError):
    """The process cannot be guaranteed to remain owned by this job."""


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class KillOnCloseJob:
    """Own one Windows job from process creation until supervisor cleanup.

    This is fail-closed: if attachment is unsupported, execution must stop.
    A handle's deterministic close revokes descendants even if the first
    process has already exited. This is not a substitute for owner authority,
    lease validation, or current runtime admission.
    """

    def __init__(self) -> None:
        if os.name != "nt":
            raise ProcessJobError("Windows job object is unavailable on this OS")
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        api.CreateJobObjectW.restype = wintypes.HANDLE
        api.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ]
        api.SetInformationJobObject.restype = wintypes.BOOL
        api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        api.AssignProcessToJobObject.restype = wintypes.BOOL
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL
        self._api = api
        self._handle = api.CreateJobObjectW(None, None)
        if not self._handle:
            raise ProcessJobError(f"CREATE_PROCESS_JOB_FAILED:{ctypes.get_last_error()}")
        settings = _ExtendedLimitInformation()
        settings.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not api.SetInformationJobObject(
            self._handle, 9, ctypes.byref(settings), ctypes.sizeof(settings),
        ):
            error = ctypes.get_last_error()
            self.close()
            raise ProcessJobError(f"CONFIGURE_PROCESS_JOB_FAILED:{error}")

    def attach(self, process: object) -> None:
        if not self._handle:
            raise ProcessJobError("PROCESS_JOB_CLOSED")
        process_handle = getattr(process, "_handle", None)
        if process_handle is None:
            raise ProcessJobError("PROCESS_HANDLE_UNAVAILABLE")
        if not self._api.AssignProcessToJobObject(
            self._handle, wintypes.HANDLE(int(process_handle)),
        ):
            raise ProcessJobError(f"ASSIGN_PROCESS_JOB_FAILED:{ctypes.get_last_error()}")

    def close(self) -> None:
        if self._handle:
            handle, self._handle = self._handle, None
            if not self._api.CloseHandle(handle):
                raise ProcessJobError(f"CLOSE_PROCESS_JOB_FAILED:{ctypes.get_last_error()}")


# CREATE_SUSPENDED must be applied to the process at creation time: simply
# assigning an already running process to a Job Object leaves a launch race.
CREATE_SUSPENDED = 0x00000004
THREAD_SUSPEND_RESUME = 0x0002
TH32CS_SNAPTHREAD = 0x00000004


class _ThreadEntry32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD),
        ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG),
        ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
    ]


def resume_suspended_process(process: object) -> None:
    """Resume the primary thread only AFTER successful Job Object attachment.

    Uses documented Toolhelp and threading APIs rather than relying on the
    private CreateProcess thread handle, which Popen closes before returning.
    On any failure the caller must terminate its still-suspended process.
    """
    if os.name != "nt":
        raise ProcessJobError("SUSPENDED_WINDOWS_PROCESS_REQUIRED")
    pid = getattr(process, "pid", None)
    if type(pid) is not int or pid < 1:
        raise ProcessJobError("SUSPENDED_PROCESS_ID_INVALID")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    api.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry32)]
    api.Thread32First.restype = wintypes.BOOL
    api.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry32)]
    api.Thread32Next.restype = wintypes.BOOL
    api.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenThread.restype = wintypes.HANDLE
    api.ResumeThread.argtypes = [wintypes.HANDLE]
    api.ResumeThread.restype = wintypes.DWORD
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL

    snapshot = api.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if not snapshot or int(snapshot) == ctypes.c_void_p(-1).value:
        raise ProcessJobError(f"THREAD_SNAPSHOT_FAILED:{ctypes.get_last_error()}")
    try:
        entry = _ThreadEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        more = api.Thread32First(snapshot, ctypes.byref(entry))
        while more:
            if entry.th32OwnerProcessID == pid:
                thread = api.OpenThread(THREAD_SUSPEND_RESUME, False, entry.th32ThreadID)
                if not thread:
                    raise ProcessJobError(f"OPEN_PRIMARY_THREAD_FAILED:{ctypes.get_last_error()}")
                try:
                    previous = api.ResumeThread(thread)
                    if previous == 0xFFFFFFFF:
                        raise ProcessJobError(f"RESUME_PRIMARY_THREAD_FAILED:{ctypes.get_last_error()}")
                    if previous != 1:
                        raise ProcessJobError(f"UNEXPECTED_THREAD_SUSPEND_COUNT:{previous}")
                    return
                finally:
                    api.CloseHandle(thread)
            more = api.Thread32Next(snapshot, ctypes.byref(entry))
        raise ProcessJobError("SUSPENDED_PRIMARY_THREAD_NOT_FOUND")
    finally:
        api.CloseHandle(snapshot)
