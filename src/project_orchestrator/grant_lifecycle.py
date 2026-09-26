from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, BinaryIO

from .state_io import atomic_json

GrantMutator = Callable[[dict[str, Any]], bool]


def grant_state_root(path: Path) -> Path:
    """Return the durable state root for legacy or unique per-grant files."""
    if path.parent.name == "hands-off-execution-grants":
        return path.parent.parent
    return path.parent


def lifecycle_path_for_grant(path: Path) -> Path:
    return grant_state_root(path) / "hands-off-grant-lifecycle.json"


def lifecycle_lock_path(lifecycle: Path) -> Path:
    return lifecycle.with_name(lifecycle.name + ".lock")


def ensure_lifecycle_lock_file(lock_path: Path) -> None:
    """Create the one-byte lock target exactly once across threads/processes."""
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if lock_path.stat().st_size >= 1:
                    return
            except FileNotFoundError:
                continue
            time.sleep(0.01)
            continue
        try:
            os.write(fd, b"0")
        finally:
            os.close(fd)
        return


def acquire_lifecycle_lock(lifecycle: Path) -> BinaryIO:
    """Acquire the kernel-owned lifecycle transaction lock."""
    lock_path = lifecycle_lock_path(lifecycle)
    ensure_lifecycle_lock_file(lock_path)
    handle = lock_path.open("r+b", buffering=0)
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        while True:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                time.sleep(0.01)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


def release_lifecycle_lock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    handle.close()


def mutate_grant_lifecycle(lifecycle: Path, grant_id: str, mutator: GrantMutator) -> bool:
    """Serialize one grant-record mutation without losing concurrent updates."""
    handle = acquire_lifecycle_lock(lifecycle)
    try:
        try:
            state = json.loads(lifecycle.read_text(encoding="utf-8"))
            record = state["grants"][grant_id]
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return False
        if not isinstance(record, dict) or not mutator(record):
            return False
        atomic_json(lifecycle, state)
        return True
    finally:
        release_lifecycle_lock(handle)
