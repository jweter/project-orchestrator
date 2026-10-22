"""Windows Job Object containment for one supervised, locally owned process.

Only this runtime's own newly created subprocess may be assigned. The Windows
kernel propagates the job to its descendants; closing the last job handle
terminates all of them. This module never interacts with arbitrary PIDs.
"""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_TH32CS_SNAPTHREAD = 0x00000004
_THREAD_SUSPEND_RESUME = 0x0002
_INVALID_HANDLE = ctypes.c_void_p(-1).value


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
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
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
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


class _ThreadEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD),
        ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG),
        ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
    ]


def _win_kernel32():
    if os.name != "nt":
        raise RuntimeError("WINDOWS_JOB_UNAVAILABLE")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)]
    kernel.Thread32First.restype = wintypes.BOOL
    kernel.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)]
    kernel.Thread32Next.restype = wintypes.BOOL
    kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenThread.restype = wintypes.HANDLE
    kernel.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel.ResumeThread.restype = wintypes.DWORD
    return kernel


class WindowsProcessJob:
    """Fail closed if the child cannot be attached before its first instruction."""

    def __init__(self) -> None:
        self._kernel = _win_kernel32()
        self._handle = self._kernel.CreateJobObjectW(None, None)
        if not self._handle:
            raise RuntimeError("PROCESS_TREE_CONTAINMENT_UNAVAILABLE")
        info = _ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._kernel.SetInformationJobObject(
            self._handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            self.close()
            raise RuntimeError("PROCESS_TREE_CONTAINMENT_UNAVAILABLE")

    def __enter__(self) -> "WindowsProcessJob":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is not None:
            self._kernel.CloseHandle(handle)

    def attach_suspended(self, process) -> None:
        """Attach the still-suspended root, before any of its code can run."""
        if self._handle is None:
            raise RuntimeError("PROCESS_TREE_CONTAINMENT_UNAVAILABLE")
        if not self._kernel.AssignProcessToJobObject(self._handle, process._handle):
            raise RuntimeError("PROCESS_TREE_CONTAINMENT_UNAVAILABLE")

    def resume(self, pid: int) -> None:
        """Resume only after a separate owner-authority check has succeeded."""
        if self._handle is None or not self._resume_primary_thread(pid):
            raise RuntimeError("PROCESS_TREE_RESUME_FAILED")

    def _resume_primary_thread(self, pid: int) -> bool:
        """Resume only a thread belonging to the suspended process we spawned."""
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            snapshot = self._kernel.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
            if snapshot == _INVALID_HANDLE:
                return False
            resumed = False
            try:
                entry = _ThreadEntry()
                entry.dwSize = ctypes.sizeof(_ThreadEntry)
                more = self._kernel.Thread32First(snapshot, ctypes.byref(entry))
                while more:
                    if entry.th32OwnerProcessID == pid:
                        thread = self._kernel.OpenThread(
                            _THREAD_SUSPEND_RESUME, False, entry.th32ThreadID
                        )
                        if thread:
                            try:
                                # ResumeThread returns the *previous*
                                # suspension count. Exactly one suspension
                                # is expected from our CREATE_SUSPENDED root.
                                # Zero means another actor already resumed it;
                                # more than one means it remains suspended.
                                # Neither can be accepted as a verified start.
                                prior_count = self._kernel.ResumeThread(thread)
                                if prior_count != 1:
                                    return False
                                resumed = True
                            finally:
                                self._kernel.CloseHandle(thread)
                        break
                    entry.dwSize = ctypes.sizeof(_ThreadEntry)
                    more = self._kernel.Thread32Next(snapshot, ctypes.byref(entry))
            finally:
                self._kernel.CloseHandle(snapshot)
            if resumed:
                return True
            time.sleep(0.01)
        return False

    def terminate(self) -> None:
        if self._handle is not None and not self._kernel.TerminateJobObject(
            self._handle, 1
        ):
            raise RuntimeError("PROCESS_TREE_TERMINATION_FAILED")
