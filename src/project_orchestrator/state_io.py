from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

ERROR_UNABLE_TO_REMOVE_REPLACED = 1175
ERROR_UNABLE_TO_MOVE_REPLACEMENT = 1176
ERROR_UNABLE_TO_MOVE_REPLACEMENT_2 = 1177


def _call_windows_replace_file(
    destination: Path,
    replacement: Path,
    backup: Path,
) -> tuple[bool, int]:
    """Call ReplaceFileW and capture GetLastError immediately."""
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
    ctypes.set_last_error(0)
    ok = bool(
        replace_file(
            str(destination.absolute()),
            str(replacement.absolute()),
            str(backup.absolute()),
            0,
            None,
            None,
        )
    )
    return ok, int(ctypes.get_last_error())


def _restore_windows_backup(backup: Path, destination: Path) -> None:
    """Restore the previous canonical file after a documented partial ReplaceFileW failure."""
    if destination.exists():
        return
    if not backup.exists():
        raise OSError("ReplaceFileW partial failure left no canonical file or backup")
    try:
        backup.replace(destination)
        return
    except PermissionError:
        restore_tmp = destination.with_name(
            f"{destination.name}.restore.{os.getpid()}.{uuid.uuid4().hex}"
        )
        try:
            shutil.copy2(backup, restore_tmp)
            restore_tmp.replace(destination)
        finally:
            restore_tmp.unlink(missing_ok=True)


def _windows_replace_file(replacement: Path, destination: Path) -> bool:
    """Use ReplaceFileW while preserving canonical state on every documented failure mode."""
    if os.name != "nt":
        return False
    backup = destination.with_name(
        f"{destination.name}.backup.{os.getpid()}.{uuid.uuid4().hex}"
    )
    try:
        try:
            succeeded, error = _call_windows_replace_file(destination, replacement, backup)
        except (AttributeError, OSError):
            return False
        if succeeded:
            backup.unlink(missing_ok=True)
            return True
        if error == ERROR_UNABLE_TO_MOVE_REPLACEMENT_2:
            _restore_windows_backup(backup, destination)
        elif error == ERROR_UNABLE_TO_MOVE_REPLACEMENT and not destination.exists():
            _restore_windows_backup(backup, destination)
        elif error == ERROR_UNABLE_TO_REMOVE_REPLACED and not destination.exists():
            _restore_windows_backup(backup, destination)
        return False
    finally:
        if destination.exists():
            backup.unlink(missing_ok=True)


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
