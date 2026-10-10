"""Durable, local-only milestone/publication state for Iron God.

This is a persistence primitive, not an acceptance authority. Only a future
integration controller that has checked immutable Git refs and exact-head
verification receipts may call record_accepted_milestone().
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
ACTIVE_PUBLICATIONS = frozenset({"PUSHING", "PR_OPEN", "CI_PENDING", "REVIEW_PENDING", "MERGE_READY"})
TRANSITIONS = {
    "READY": frozenset({"PUSHING", "DEFERRED"}),
    "DEFERRED": frozenset({"PUSHING"}),
    "PUSHING": frozenset({"DEFERRED", "PR_OPEN"}),
    "PR_OPEN": frozenset({"CI_PENDING", "DEFERRED"}),
    "CI_PENDING": frozenset({"CI_FAILED", "REVIEW_PENDING", "DEFERRED"}),
    "CI_FAILED": frozenset({"DEFERRED"}),
    "REVIEW_PENDING": frozenset({"MERGE_READY", "DEFERRED"}),
    "MERGE_READY": frozenset({"MERGED", "DEFERRED"}),
    "MERGED": frozenset({"RELEASE_SYNCED"}),
    "RELEASE_SYNCED": frozenset(),
}


class LocalStateError(ValueError):
    """A state invariant was violated; never silently repair it."""


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate(repo: str, milestone_id: str, sha: str, receipt_hash: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repo):
        raise LocalStateError("INVALID_REPOSITORY")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", milestone_id):
        raise LocalStateError("INVALID_MILESTONE_ID")
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise LocalStateError("INVALID_COMMIT_SHA")
    if not re.fullmatch(r"[a-f0-9]{64}", receipt_hash):
        raise LocalStateError("INVALID_VERIFICATION_HASH")


class LocalFirstState:
    """Transactional queue with recovery after process death/reopen.

    Git verification, owner control, real Product Reality, and publication
    transport belong to external controllers, not this state store.
    """

    def __init__(self, db_path: Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            # Acquire the schema writer lease BEFORE reading the version.
            # Concurrent workers must never observe half-migrated state.
            with db:
                db.execute("BEGIN IMMEDIATE")
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, SCHEMA_VERSION):
                    raise LocalStateError("UNSUPPORTED_SCHEMA_VERSION")
                if version == 0:
                    db.execute("""
                        CREATE TABLE milestones (
                            milestone_id TEXT PRIMARY KEY,
                            repository TEXT NOT NULL,
                            accepted_sha TEXT NOT NULL,
                            verification_sha256 TEXT NOT NULL,
                            product_reality TEXT NOT NULL
                                CHECK(product_reality IN ('PASSED', 'NOT_REQUIRED')),
                            accepted_at TEXT NOT NULL
                        )
                    """)
                    db.execute("""
                        CREATE TABLE publications (
                            milestone_id TEXT PRIMARY KEY REFERENCES milestones(milestone_id),
                            repository TEXT NOT NULL,
                            state TEXT NOT NULL,
                            last_error TEXT,
                            changed_at TEXT NOT NULL
                        )
                    """)
                    db.execute(
                        "CREATE INDEX publications_by_repo "
                        "ON publications(repository, state, changed_at)"
                    )
                    db.execute("""
                        CREATE TABLE journal (
                            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                            milestone_id TEXT NOT NULL,
                            event_type TEXT NOT NULL,
                            detail TEXT NOT NULL,
                            occurred_at TEXT NOT NULL
                        )
                    """)
                    db.execute("PRAGMA user_version = 1")
            result = db.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise LocalStateError("DATABASE_INTEGRITY_FAILED")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def record_accepted_milestone(
        self, *, repository: str, milestone_id: str, accepted_sha: str,
        verification_sha256: str, product_reality: str,
    ) -> bool:
        """Persist verified external-controller acceptance and queue publication.

        Identical replays are harmless. Divergent replays fail closed.
        Returns True only for the first insertion.
        """
        _validate(repository, milestone_id, accepted_sha, verification_sha256)
        if product_reality not in ("PASSED", "NOT_REQUIRED"):
            raise LocalStateError("PRODUCT_REALITY_NOT_CLEARED")
        with closing(self._connect()) as db:
            with db:
                db.execute("BEGIN IMMEDIATE")
                existing = db.execute(
                    "SELECT repository,accepted_sha,verification_sha256,product_reality "
                    "FROM milestones WHERE milestone_id=?", (milestone_id,),
                ).fetchone()
                if existing is not None:
                    if tuple(existing) != (
                        repository, accepted_sha, verification_sha256, product_reality
                    ):
                        raise LocalStateError("MILESTONE_IDENTITY_CONFLICT")
                    return False
                now = _utc()
                db.execute(
                    "INSERT INTO milestones VALUES (?,?,?,?,?,?)",
                    (milestone_id, repository, accepted_sha, verification_sha256,
                     product_reality, now),
                )
                db.execute(
                    "INSERT INTO publications VALUES (?,?,?,?,?)",
                    (milestone_id, repository, "READY", None, now),
                )
                db.execute(
                    "INSERT INTO journal(milestone_id,event_type,detail,occurred_at) "
                    "VALUES (?,?,?,?)",
                    (milestone_id, "MILESTONE_ACCEPTED", accepted_sha, now),
                )
                db.execute(
                    "INSERT INTO journal(milestone_id,event_type,detail,occurred_at) "
                    "VALUES (?,?,?,?)",
                    (milestone_id, "PUBLICATION_QUEUED", "READY", now),
                )
                return True

    def transition_publication(
        self, *, milestone_id: str, expected_state: str, next_state: str,
        detail: str = "",
    ) -> None:
        """Optimistic, atomic publication transition; never changes acceptance."""
        if not isinstance(detail, str) or len(detail) > 2000:
            raise LocalStateError("INVALID_DETAIL")
        if next_state not in TRANSITIONS.get(expected_state, frozenset()):
            raise LocalStateError("INVALID_PUBLICATION_TRANSITION")
        with closing(self._connect()) as db:
            with db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT repository,state FROM publications WHERE milestone_id=?",
                    (milestone_id,),
                ).fetchone()
                if row is None or row["state"] != expected_state:
                    raise LocalStateError("PUBLICATION_STATE_CHANGED")
                if next_state in ACTIVE_PUBLICATIONS and expected_state not in ACTIVE_PUBLICATIONS:
                    concurrent = db.execute(
                        "SELECT 1 FROM publications WHERE repository=? "
                        "AND milestone_id<>? AND state IN ("
                        "'PUSHING','PR_OPEN','CI_PENDING','REVIEW_PENDING','MERGE_READY') LIMIT 1",
                        (row["repository"], milestone_id),
                    ).fetchone()
                    if concurrent:
                        raise LocalStateError("REPOSITORY_PUBLICATION_LANE_OCCUPIED")
                now = _utc()
                db.execute(
                    "UPDATE publications SET state=?,last_error=?,changed_at=? "
                    "WHERE milestone_id=?",
                    (next_state, detail or None, now, milestone_id),
                )
                db.execute(
                    "INSERT INTO journal(milestone_id,event_type,detail,occurred_at) "
                    "VALUES (?,?,?,?)",
                    (milestone_id, "PUBLICATION_" + next_state, detail, now),
                )

    def snapshot(self) -> dict:
        """Return a read-only summary suitable for diagnostics, not proof of Git state."""
        with closing(self._connect()) as db:
            milestones = [
                dict(row) for row in db.execute(
                    "SELECT * FROM milestones ORDER BY accepted_at,milestone_id"
                )
            ]
            publications = [
                dict(row) for row in db.execute(
                    "SELECT * FROM publications ORDER BY changed_at,milestone_id"
                )
            ]
            journal = [
                dict(row) for row in db.execute(
                    "SELECT * FROM journal ORDER BY event_id"
                )
            ]
        return {
            "schema_version": SCHEMA_VERSION,
            "milestones": milestones,
            "publications": publications,
            "journal": journal,
        }
