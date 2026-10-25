"""Fail-closed Windows process-tree containment for supervised workers.

A job object owns the process before it can spawn long-lived descendants.
KILL_ON_JOB_CLOSE also covers unexpected supervisor-process termination.
This module is used only by the native Windows supervised runtime.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes


class _BasicLimits(ctypes.Structure):
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
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class WindowsJobScope:
    """Assign an already-started subprocess to a fail-closed Windows job."""

    def __init__(self, process):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = (
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        )
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        kernel.TerminateJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        try:
            limit = _ExtendedLimits()
            limit.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(
                handle, 9, ctypes.byref(limit), ctypes.sizeof(limit)
            ):
                raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
            if not kernel.AssignProcessToJobObject(
                handle, wintypes.HANDLE(int(process._handle))
            ):
                raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
        except BaseException:
            kernel.CloseHandle(handle)
            raise
        self._kernel = kernel
        self._handle = handle

    def terminate(self) -> None:
        if self._handle and not self._kernel.TerminateJobObject(self._handle, 1):
            raise OSError(ctypes.get_last_error(), "TerminateJobObject failed")

    def close(self) -> None:
        if self._handle:
            handle, self._handle = self._handle, None
            if not self._kernel.CloseHandle(handle):
                raise OSError(ctypes.get_last_error(), "CloseHandle job failed")
