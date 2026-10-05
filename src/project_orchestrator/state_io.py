from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any


def _windows_replace_file(replacement: Path, destination: Path) -> bool:
    """Use the Windows replacement primitive when MoveFileEx-style replace is denied."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        replace_file = ctypes.WinDLL("kernel32", use_last_error=True).ReplaceFileW
        replace_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        replace_file.restype = wintypes.BOOL
        return bool(
            replace_file(
                str(destination.absolute()),
                str(replacement.absolute()),
                None,
                0,
                None,
                None,
            )
        )
    except (AttributeError, OSError):
        return False


def atomic_json(path: Path, payload: dict[str, Any], *, replace_attempts: int = 5) -> None:
    """Persist JSON safely across concurrent Windows readers/writers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
    try:
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        attempts = max(1, replace_attempts)
        for attempt in range(attempts):
            try:
                tmp.replace(path)
                return
            except PermissionError:
                if attempt == attempts - 1:
                    if _windows_replace_file(tmp, path):
                        return
                    raise
                time.sleep(min(0.1 * (attempt + 1), 0.5))
    finally:
        tmp.unlink(missing_ok=True)

def optional_atomic_json(path: Path, payload: dict[str, Any], *, replace_attempts: int = 5) -> bool:
    """Publish redundant telemetry atomically without taking down its owner on contention."""
    try:
        atomic_json(path, payload, replace_attempts=replace_attempts)
    except PermissionError:
        return False
    return True
