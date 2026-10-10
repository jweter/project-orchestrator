"""Shadow-only Iron God SQLite health, backup and deterministic JSON exports.

Never grants development, integration, publication or merge authority.
"""
from __future__ import annotations

import ctypes
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from project_orchestrator.local_state import LocalStateStore, SCHEMA_VERSION


def _readonly(path: Path, *, live: bool = False) -> sqlite3.Connection:
    """Read an existing DB without creating WAL sidecars for health inspection.

    Online backup permits SQLite-managed WAL access while owner release is held.
    Quiescent health inspection uses immutable=1; an inconsistent sidecar pair
    fails closed rather than making missing files during a user-owned session.
    """
    if not path.is_file():
        raise FileNotFoundError(path)
    wal = Path(str(path) + "-wal")
    shm = Path(str(path) + "-shm")
    if not live and wal.exists() != shm.exists():
        raise OSError("incomplete WAL sidecars")
    immutable = not live and not wal.exists() and not shm.exists()
    uri = f"{path.resolve().as_uri()}?mode=ro"
    if immutable:
        uri += "&immutable=1"
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def _trigger_valid(db: sqlite3.Connection, name: str, operation: str) -> bool:
    """Match the canonical, unconditional abort-trigger body, not just its name."""
    row = db.execute(
        "SELECT tbl_name, sql FROM sqlite_master WHERE type='trigger' AND name=?",
        (name,),
    ).fetchone()
    if row is None or row["tbl_name"] != "events" or not row["sql"]:
        return False
    body = "".join(str(row["sql"]).casefold().split())
    expected = (
        f"create trigger if not exists {name} before {operation} on events "
        "begin select raise(abort, 'immutable event'); end"
    )
    canonical = "".join(expected.casefold().split())
    without_optional_clause = canonical.replace("ifnotexists", "", 1)
    return body in {canonical, without_optional_clause}


def _invalid_claims(db: sqlite3.Connection) -> int:
    """Check the complete immutable claim receipt against every live lease."""
    invalid = 0
    for lease in db.execute(
        "SELECT resource,lease_id,owner_id,owner_fingerprint FROM leases"
    ):
        event = db.execute(
            "SELECT kind,subject,evidence_json FROM events WHERE event_id=?",
            ("lease-claimed:" + lease["lease_id"],),
        ).fetchone()
        if event is None or event["kind"] != "WORKER_CLAIMED" or event["subject"] != lease["resource"]:
            invalid += 1
            continue
        try:
            evidence = json.loads(event["evidence_json"])
        except (ValueError, TypeError):
            invalid += 1
            continue
        if not isinstance(evidence, dict) or evidence != {
            "lease_id": lease["lease_id"],
            "owner_id": lease["owner_id"],
            "owner_fingerprint": lease["owner_fingerprint"],
        }:
            invalid += 1
    return invalid


def health_check(store: LocalStateStore) -> dict[str, object]:
    """Non-mutating state inspection. Any ambiguous evidence is UNHEALTHY."""
    try:
        with closing(_readonly(store.database)) as db:
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            # SQLite immutable readers report journal_mode=off; derive WAL mode
            # from the durable database header without opening writable sidecars.
            with store.database.open("rb") as source_header:
                source_header.seek(18)
                journal_bytes = source_header.read(2)
            mode = "wal" if journal_bytes == bytes((2, 2)) else db.execute("PRAGMA journal_mode").fetchone()[0]
            version = db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            event_count = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            lease_count = db.execute("SELECT COUNT(*) FROM leases").fetchone()[0]
            invalid_claims = _invalid_claims(db)
            triggers_valid = all((
                _trigger_valid(db, "events_immutable_update", "update"),
                _trigger_valid(db, "events_immutable_delete", "delete"),
            ))
        reasons = []
        if integrity != "ok":
            reasons.append("SQLITE_INTEGRITY_FAILED")
        if version != SCHEMA_VERSION:
            reasons.append("SCHEMA_VERSION_UNEXPECTED")
        if mode.lower() != "wal":
            reasons.append("WAL_NOT_ENABLED")
        if invalid_claims:
            reasons.append("LEASE_CLAIM_EVIDENCE_INVALID")
        if not triggers_valid:
            reasons.append("EVENT_IMMUTABILITY_TRIGGERS_INVALID")
        return {
            "status": "HEALTHY" if not reasons else "UNHEALTHY",
            "reasons": reasons,
            "schema_version": version,
            "event_count": event_count,
            "lease_count": lease_count,
        }
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        return {
            "status": "UNHEALTHY", "reasons": [type(exc).__name__],
            "schema_version": None, "event_count": None, "lease_count": None,
        }


def _require_new_target(target: Path) -> None:
    if target.exists():
        raise FileExistsError(f"refusing to overwrite existing checkpoint: {target}")
    if not target.parent.is_dir():
        raise FileNotFoundError(f"checkpoint directory missing: {target.parent}")


def _publish_durably(temporary: Path, target: Path) -> None:
    """Atomic no-overwrite publication with metadata durability.

    Windows MoveFileExW with WRITE_THROUGH is the supported Windows metadata
    durability boundary. POSIX hardlink plus directory fsync covers the same
    requirement without replacing a preexisting user file.
    """
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        move = kernel.MoveFileExW
        move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        move.restype = ctypes.c_int
        MOVEFILE_WRITE_THROUGH = 0x00000008
        if not move(str(temporary), str(target), MOVEFILE_WRITE_THROUGH):
            raise ctypes.WinError(ctypes.get_last_error())
        return

    os.link(temporary, target)
    descriptor = os.open(str(target.parent), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
        temporary.unlink()
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def online_backup(store: LocalStateStore, target: Path) -> dict[str, object]:
    """Create a consistent, standalone SQLite backup while live writers may run."""
    store._require_owner_release()
    target = Path(target)
    _require_new_target(target)
    if health_check(store)["status"] != "HEALTHY":
        raise RuntimeError("source database is not healthy")
    temporary = target.with_name("." + target.name + ".pending-" + uuid4().hex)
    try:
        with closing(_readonly(store.database, live=True)) as source:
            with closing(sqlite3.connect(str(temporary))) as destination:
                source.backup(destination)
                if destination.execute("PRAGMA journal_mode=DELETE").fetchone()[0].lower() != "delete":
                    raise RuntimeError("checkpoint could not be sealed without WAL")
                destination.execute("PRAGMA synchronous=FULL")
        with temporary.open("r+b") as file:
            os.fsync(file.fileno())
        with closing(_readonly(temporary)) as backup_db:
            if backup_db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("backup integrity check failed")
            version = backup_db.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
            if version != SCHEMA_VERSION:
                raise RuntimeError("backup schema version mismatch")
            events = backup_db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            leases = backup_db.execute("SELECT COUNT(*) FROM leases").fetchone()[0]
        store._require_owner_release()
        _require_new_target(target)
        _publish_durably(temporary, target)
        return {
            "status": "BACKED_UP", "schema_version": version,
            "event_count": events, "lease_count": leases, "path": str(target),
        }
    finally:
        if temporary.exists():
            temporary.unlink()


def export_snapshot(store: LocalStateStore, target: Path) -> dict[str, object]:
    """Deterministic compatibility export, not an authoritative state store."""
    store._require_owner_release()
    target = Path(target)
    _require_new_target(target)
    if health_check(store)["status"] != "HEALTHY":
        raise RuntimeError("source database is not healthy")
    data = store.snapshot()
    content = (
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        + "\n"
    ).encode("utf-8")
    temporary = target.with_name("." + target.name + ".pending-" + uuid4().hex)
    try:
        with temporary.open("xb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        store._require_owner_release()
        _require_new_target(target)
        _publish_durably(temporary, target)
        return {
            "status": "EXPORTED", "path": str(target), "bytes": len(content),
            "events": len(data["events"]), "leases": len(data["leases"]),
        }
    finally:
        if temporary.exists():
            temporary.unlink()
