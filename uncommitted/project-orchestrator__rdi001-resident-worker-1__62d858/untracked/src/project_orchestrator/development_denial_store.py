"""Crash-resistant denial snapshots for an explicitly assigned development workspace.

This is NOT a controller authority store. No process, worker, or Git actions.
An absent history fails closed unless initialization is explicitly requested.
"""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .development_denial_ledger import DenialLedger


@contextmanager
def _exclusive_lock(path: Path):
    """Serialize writers using a sibling lock file, without changing the ledger."""
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            if not handle.read(1):
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _check_path(path: Path) -> Path:
    path = Path(path)
    if not path.parent.is_dir() or path.is_symlink():
        raise ValueError("DENIAL_STORE_PATH_UNTRUSTED")
    return path


def load_denials(path: Path) -> DenialLedger:
    """Never interpret a missing or corrupt history as an empty allowlist."""
    path = _check_path(path)
    try:
        raw = path.read_bytes()
        if len(raw) > 1024 * 1024:
            raise ValueError("DENIAL_STORE_TOO_LARGE")
        return DenialLedger.parse(json.loads(raw))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("DENIAL_STORE_MISSING_OR_CORRUPT") from exc


def persist_denials(
    path: Path, ledger: DenialLedger, *, allow_initialize: bool = False
) -> None:
    """Atomically replace a snapshot only when prior denials remain unchanged."""
    path = _check_path(path)
    if not isinstance(ledger, DenialLedger):
        raise ValueError("DENIAL_LEDGER_UNVERIFIED")
    # Revalidate caller data before touching the snapshot.
    ledger = DenialLedger.parse(ledger.as_dict())
    lock_path = path.with_name(path.name + ".lock")
    if lock_path.is_symlink():
        raise ValueError("DENIAL_LOCK_PATH_UNTRUSTED")
    with _exclusive_lock(lock_path):
        if path.exists():
            existing = load_denials(path)
            original = {entry.operation_id: entry.reason for entry in existing.denials}
            updated = {entry.operation_id: entry.reason for entry in ledger.denials}
            if any(updated.get(key) != reason for key, reason in original.items()):
                raise ValueError("DENIAL_HISTORY_ROLLBACK_FORBIDDEN")
        elif not allow_initialize:
            raise ValueError("DENIAL_STORE_INITIALIZATION_NOT_AUTHORIZED")
        payload = json.dumps(ledger.as_dict(), sort_keys=True, separators=(",", ":"))
        temporary = None
        try:
            fd, name = tempfile.mkstemp(prefix=".denials-", suffix=".tmp", dir=path.parent)
            temporary = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
