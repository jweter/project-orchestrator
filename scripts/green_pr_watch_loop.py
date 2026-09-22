"""Lightweight always-on green PR watcher. Heavy development remains idle-gated."""

from __future__ import annotations
import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

try:
    from scripts.green_pr_merger import cycle
except ModuleNotFoundError:
    from green_pr_merger import cycle


def acquire_singleton(state: Path) -> BinaryIO | None:
    """Hold one OS-level lock for the watcher lifetime; duplicates exit cleanly."""
    lock_path = state.with_name(f"{state.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b", buffering=0)
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def release_singleton(handle: BinaryIO) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    tmp.replace(path)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--state", required=True)
    p.add_argument("--poll-seconds", type=int, default=60)
    p.add_argument("--once", action="store_true")
    a = p.parse_args()
    state = Path(a.state)
    singleton = acquire_singleton(state)
    if singleton is None:
        return 0
    try:
        while True:
            started = time.time()
            try:
                results = cycle(False)
                error = None
            except Exception as exc:
                results = []
                error = type(exc).__name__
            atomic(
                state,
                {
                    "updated_at_utc": datetime.now(UTC).isoformat(),
                    "duration_seconds": round(time.time() - started, 3),
                    "error_class": error,
                    "results": results,
                },
            )
            if a.once:
                return 0 if error is None else 1
            time.sleep(max(30, a.poll_seconds))
    finally:
        release_singleton(singleton)


if __name__ == "__main__":
    raise SystemExit(main())
