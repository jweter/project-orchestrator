"""Shadow-only Iron God SQLite health, backup and deterministic JSON exports.

Does not schedule workers, integrate candidates or publish to GitHub.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from project_orchestrator.local_state import LocalStateStore, SCHEMA_VERSION


def _readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def health_check(store: LocalStateStore) -> dict[str, object]:
    """Read-only assessment; no database creation or false-green on error."""
    try:
        with closing(_readonly(store.database)) as db:
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            mode = db.execute("PRAGMA journal_mode").fetchone()[0]
            version = db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            event_count = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            lease_count = db.execute("SELECT COUNT(*) FROM leases").fetchone()[0]
            missing_claims = db.execute(
                """SELECT COUNT(*) FROM leases l
                   LEFT JOIN events e ON e.event_id='lease-claimed:' || l.lease_id
                   WHERE e.sequence IS NULL"""
            ).fetchone()[0]
            triggers = {
                row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                )
            }
        reasons = []
        if integrity != "ok":
            reasons.append("SQLITE_INTEGRITY_FAILED")
        if version != SCHEMA_VERSION:
            reasons.append("SCHEMA_VERSION_UNEXPECTED")
        if mode.lower() != "wal":
            reasons.append("WAL_NOT_ENABLED")
        if missing_claims:
            reasons.append("LEASE_WITHOUT_CLAIM_EVENT")
        if not {"events_immutable_update", "events_immutable_delete"} <= triggers:
            reasons.append("EVENT_IMMUTABILITY_TRIGGERS_MISSING")
        return {
            "status": "HEALTHY" if not reasons else "UNHEALTHY",
            "reasons": reasons,
            "schema_version": version,
            "event_count": event_count,
            "lease_count": lease_count,
        }
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        return {"status": "UNHEALTHY", "reasons": [type(exc).__name__],
                "schema_version": None, "event_count": None, "lease_count": None}


def _require_new_target(target: Path) -> None:
    if target.exists():
        raise FileExistsError(f"refusing to overwrite existing checkpoint: {target}")
    if not target.parent.is_dir():
        raise FileNotFoundError(f"checkpoint directory missing: {target.parent}")


def online_backup(store: LocalStateStore, target: Path) -> dict[str, object]:
    """Create a consistent SQLite backup from WAL without stopping live writers.

    A backup can never silently overwrite another backup. Temporary files are
    removed on veto/failure. No scheduler or release authority is derived.
    """
    store._require_owner_release()
    target = Path(target)
    _require_new_target(target)
    health = health_check(store)
    if health["status"] != "HEALTHY":
        raise RuntimeError("source database is not healthy")
    temporary = target.with_name("." + target.name + ".pending-" + uuid4().hex)
    try:
        with closing(_readonly(store.database)) as source:
            with closing(sqlite3.connect(str(temporary))) as destination:
                source.backup(destination)
                # Portable checkpoint files must not require an external WAL sidecar.
                if destination.execute("PRAGMA journal_mode=DELETE").fetchone()[0].lower() != "delete":
                    raise RuntimeError("checkpoint could not be sealed without WAL")
                destination.execute("PRAGMA synchronous=FULL")
        with temporary.open("r+b") as file:
            os.fsync(file.fileno())
        with closing(_readonly(temporary)) as backup_db:
            if backup_db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("backup integrity check failed")
            versions = backup_db.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
            if versions != SCHEMA_VERSION:
                raise RuntimeError("backup schema version mismatch")
            events = backup_db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            leases = backup_db.execute("SELECT COUNT(*) FROM leases").fetchone()[0]
        store._require_owner_release()
        _require_new_target(target)
        # Same-directory hardlink publishes the complete file atomically and
        # fails if another actor created target after the final existence check.
        os.link(temporary, target)
        temporary.unlink()
        return {"status": "BACKED_UP", "schema_version": versions,
                "event_count": events, "lease_count": leases,
                "path": str(target)}
    finally:
        if temporary.exists():
            temporary.unlink()


def export_snapshot(store: LocalStateStore, target: Path) -> dict[str, object]:
    """Write a stable sorted JSON projection; never change the SQLite authority."""
    store._require_owner_release()
    target = Path(target)
    _require_new_target(target)
    source_health = health_check(store)
    if source_health["status"] != "HEALTHY":
        raise RuntimeError("source database is not healthy")
    data = store.snapshot()
    content = (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False,
                          allow_nan=False) + "\n").encode("utf-8")
    temporary = target.with_name("." + target.name + ".pending-" + uuid4().hex)
    try:
        with temporary.open("xb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        store._require_owner_release()
        _require_new_target(target)
        # Same-directory hardlink publishes the complete file atomically and
        # fails if another actor created target after the final existence check.
        os.link(temporary, target)
        temporary.unlink()
        return {"status": "EXPORTED", "path": str(target), "bytes": len(content),
                "events": len(data["events"]), "leases": len(data["leases"])}
    finally:
        if temporary.exists():
            temporary.unlink()
