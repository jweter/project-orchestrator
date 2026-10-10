"""Iron God shadow-only transactional state; not wired into production scheduling.

SQLite is a projection of the append-only event journal. Protected Git refs and
verification receipts remain the authority for code, not this database.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4


SCHEMA_VERSION = 1


class OwnerControlBlocked(RuntimeError):
    """Owner-control marker is active, missing, or unreadable."""


class LeaseConflict(RuntimeError):
    """A resource has an unreconciled owner; TTL alone cannot transfer it."""


class StateConflict(RuntimeError):
    """An idempotency key was reused for different immutable evidence."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


class LocalStateStore:
    """One-operation connections with immediate transactions and durable writes.

    The explicit owner_state_path is deliberate: shadow instances must never infer
    owner authority from their own temporary database directory.
    """

    def __init__(self, database: Path, *, owner_state_path: Path) -> None:
        self.database = Path(database)
        self.owner_state_path = Path(owner_state_path)

    def _require_owner_release(self) -> None:
        try:
            owner = json.loads(self.owner_state_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, TypeError) as exc:
            raise OwnerControlBlocked("OWNER_CONTROL_UNVERIFIED") from exc
        if not isinstance(owner, dict) or type(owner.get("enabled")) is not bool:
            raise OwnerControlBlocked("OWNER_CONTROL_UNVERIFIED")
        if owner["enabled"]:
            raise OwnerControlBlocked("OWNER_CONTROL_ACTIVE")

    def _connect(self) -> sqlite3.Connection:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.database), timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        return db

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        self._require_owner_release()
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._require_owner_release()
                yield db
                self._require_owner_release()
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    def initialize(self) -> None:
        """Install schema v1; deterministic, repeatable, without deleting prior data."""
        self._require_owner_release()
        with closing(self._connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
        with self._write() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
            )""")
            current = db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            if current is not None and int(current) > SCHEMA_VERSION:
                raise StateConflict("future schema; downgrade refused")
            if current is None:
                db.execute("""CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                )""")
                db.execute("""CREATE TABLE IF NOT EXISTS leases (
                    resource TEXT PRIMARY KEY,
                    lease_id TEXT NOT NULL UNIQUE,
                    owner_id TEXT NOT NULL,
                    owner_fingerprint TEXT NOT NULL,
                    claimed_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL
                )""")
                db.execute("""CREATE TRIGGER IF NOT EXISTS events_immutable_update
                    BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'immutable event'); END""")
                db.execute("""CREATE TRIGGER IF NOT EXISTS events_immutable_delete
                    BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'immutable event'); END""")
                db.execute("INSERT INTO schema_migrations(version, applied_at) VALUES(?, ?)",
                           (SCHEMA_VERSION, _now()))
            elif int(current) != SCHEMA_VERSION:
                raise StateConflict("unsupported schema migration")

    @staticmethod
    def _event(db: sqlite3.Connection, event_id: str, kind: str,
               subject: str, payload: dict[str, object]) -> int:
        if not event_id or not kind or not subject:
            raise ValueError("event id, kind, and subject must be non-empty")
        evidence = _canonical(payload)
        existing = db.execute(
            "SELECT sequence,kind,subject,evidence_json FROM events WHERE event_id=?",
            (event_id,),
        ).fetchone()
        if existing:
            if (existing["kind"], existing["subject"], existing["evidence_json"]) != (
                kind, subject, evidence
            ):
                raise StateConflict("conflicting event replay")
            return int(existing["sequence"])
        db.execute(
            "INSERT INTO events(event_id,kind,subject,evidence_json,recorded_at) VALUES(?,?,?,?,?)",
            (event_id, kind, subject, evidence, _now()),
        )
        return int(db.execute("SELECT last_insert_rowid()").fetchone()[0])

    def append_event(self, event_id: str, kind: str, subject: str,
                     payload: dict[str, object]) -> int:
        """Identical retries preserve event sequence; mismatched retries fail closed."""
        if event_id.startswith(("lease-claimed:", "lease-released:")):
            raise ValueError("lease lifecycle event IDs are reserved for internal transitions")
        with self._write() as db:
            return self._event(db, event_id, kind, subject, payload)

    def claim_lease(self, resource: str, owner_id: str, owner_fingerprint: str,
                    *, lease_id: str | None = None) -> str:
        """Atomically reserve once. Never steal an expired or orphaned lease."""
        if not all((resource, owner_id, owner_fingerprint)):
            raise ValueError("resource and owner identity required")
        token = lease_id or uuid4().hex
        with self._write() as db:
            if db.execute("SELECT 1 FROM events WHERE event_id=?", ("lease-claimed:" + token,)).fetchone():
                raise LeaseConflict("previously used lease token")
            try:
                db.execute(
                    """INSERT INTO leases(resource,lease_id,owner_id,owner_fingerprint,
                       claimed_at,heartbeat_at) VALUES(?,?,?,?,?,?)""",
                    (resource, token, owner_id, owner_fingerprint, _now(), _now()),
                )
            except sqlite3.IntegrityError as exc:
                raise LeaseConflict("resource or token already claimed") from exc
            self._event(db, "lease-claimed:" + token, "WORKER_CLAIMED", resource,
                        {"lease_id": token, "owner_id": owner_id,
                         "owner_fingerprint": owner_fingerprint})
        return token

    def heartbeat(self, resource: str, lease_id: str, owner_fingerprint: str) -> bool:
        with self._write() as db:
            result = db.execute(
                """UPDATE leases SET heartbeat_at=? WHERE resource=? AND lease_id=?
                    AND owner_fingerprint=?""",
                (_now(), resource, lease_id, owner_fingerprint),
            )
            return result.rowcount == 1

    def release_lease(self, resource: str, lease_id: str,
                      owner_fingerprint: str) -> bool:
        with self._write() as db:
            row = db.execute(
                """SELECT owner_id FROM leases WHERE resource=? AND lease_id=?
                   AND owner_fingerprint=?""",
                (resource, lease_id, owner_fingerprint),
            ).fetchone()
            if row is None:
                return False
            db.execute("DELETE FROM leases WHERE resource=? AND lease_id=?",
                       (resource, lease_id))
            self._event(db, "lease-released:" + lease_id, "WORKER_RELEASED", resource,
                        {"lease_id": lease_id, "owner_id": row["owner_id"]})
            return True

    def snapshot(self) -> dict[str, object]:
        """Read-only projection; no owner-control mutation authority required."""
        if not self.database.is_file():
            raise FileNotFoundError(self.database)
        db = sqlite3.connect(f"{self.database.resolve().as_uri()}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            version = db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            if version != SCHEMA_VERSION:
                raise StateConflict("unrecognized schema version")
            events = [dict(row) for row in db.execute(
                "SELECT * FROM events ORDER BY sequence")]
            leases = [dict(row) for row in db.execute(
                "SELECT * FROM leases ORDER BY resource")]
            return {"schema_version": version, "events": events, "leases": leases}
        finally:
            db.close()

    def integrity_check(self) -> bool:
        if not self.database.is_file():
            return False
        db = sqlite3.connect(f"{self.database.resolve().as_uri()}?mode=ro", uri=True)
        try:
            return db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            db.close()
