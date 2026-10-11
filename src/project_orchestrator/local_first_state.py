"""Durable, local-only milestone/publication state for Iron God.

This is a persistence primitive, not an acceptance authority. Only a future
integration controller that has checked immutable Git refs and exact-head
verification receipts may call record_accepted_milestone().
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 2
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
                if version not in (0, 1, SCHEMA_VERSION):
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
                if version in (0, 1):
                    db.execute("""
                        CREATE TABLE acceptance_intents (
                            milestone_id TEXT PRIMARY KEY,
                            repository TEXT NOT NULL,
                            accepted_base_sha TEXT NOT NULL,
                            accepted_sha TEXT NOT NULL,
                            verification_sha256 TEXT NOT NULL,
                            product_reality TEXT NOT NULL
                                CHECK(product_reality IN ('PASSED', 'NOT_REQUIRED')),
                            context_json TEXT NOT NULL,
                            state TEXT NOT NULL
                                CHECK(state IN ('PREPARED', 'COMMITTED')),
                            created_at TEXT NOT NULL,
                            changed_at TEXT NOT NULL
                        )
                    """)
                    db.execute("""
                        CREATE UNIQUE INDEX one_pending_acceptance_per_repository
                        ON acceptance_intents(repository) WHERE state='PREPARED'
                    """)
                    db.execute("PRAGMA user_version = 2")
            result = db.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise LocalStateError("DATABASE_INTEGRITY_FAILED")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        # Busy timeout must precede WAL setup: first-time concurrent openers
        # otherwise contend on journal-mode initialization and fail early.
        db.execute("PRAGMA busy_timeout=10000")
        # Concurrent first-open WAL negotiation can transiently return BUSY
        # on Windows even after busy_timeout is configured.
        for attempt in range(10):
            try:
                db.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 9:
                    db.close()
                    raise
                import time
                time.sleep(0.05 * (attempt + 1))
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA foreign_keys=ON")
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
                pending = db.execute(
                    "SELECT milestone_id FROM acceptance_intents "
                    "WHERE repository=? AND state='PREPARED' LIMIT 1",
                    (repository,),
                ).fetchone()
                if pending is not None:
                    raise LocalStateError("REPOSITORY_ACCEPTANCE_LANE_OCCUPIED")
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

    def prepare_acceptance(
        self, *, repository: str, milestone_id: str, accepted_base_sha: str,
        accepted_sha: str, verification_sha256: str, product_reality: str,
        context_json: str,
    ) -> bool:
        """Durably fence a single in-flight Git CAS before any ref is advanced.

        Replaying the same identity is safe; competing or divergent writes
        fail closed. Context is immutable recovery metadata, not authority.
        """
        _validate(repository, milestone_id, accepted_sha, verification_sha256)
        if not isinstance(accepted_base_sha, str) or not re.fullmatch(
            r"[a-f0-9]{40}", accepted_base_sha
        ):
            raise LocalStateError("INVALID_ACCEPTED_BASE")
        if product_reality not in ("PASSED", "NOT_REQUIRED"):
            raise LocalStateError("PRODUCT_REALITY_NOT_CLEARED")
        if not isinstance(context_json, str) or len(context_json) > 8192:
            raise LocalStateError("INVALID_ACCEPTANCE_CONTEXT")
        try:
            if not isinstance(json.loads(context_json), dict):
                raise LocalStateError("INVALID_ACCEPTANCE_CONTEXT")
        except json.JSONDecodeError as exc:
            raise LocalStateError("INVALID_ACCEPTANCE_CONTEXT") from exc
        identity = (
            repository, accepted_base_sha, accepted_sha,
            verification_sha256, product_reality, context_json,
        )
        with closing(self._connect()) as db:
            with db:
                db.execute("BEGIN IMMEDIATE")
                previous = db.execute(
                    "SELECT repository,accepted_base_sha,accepted_sha,"
                    "verification_sha256,product_reality,context_json "
                    "FROM acceptance_intents WHERE milestone_id=?",
                    (milestone_id,),
                ).fetchone()
                if previous is not None:
                    if tuple(previous) != identity:
                        raise LocalStateError("ACCEPTANCE_INTENT_IDENTITY_CONFLICT")
                    return False
                if db.execute(
                    "SELECT 1 FROM milestones WHERE milestone_id=?", (milestone_id,),
                ).fetchone():
                    raise LocalStateError("MILESTONE_IDENTITY_CONFLICT")
                if db.execute(
                    "SELECT 1 FROM acceptance_intents WHERE repository=? "
                    "AND state='PREPARED' LIMIT 1", (repository,),
                ).fetchone():
                    raise LocalStateError("REPOSITORY_ACCEPTANCE_LANE_OCCUPIED")
                now = _utc()
                db.execute(
                    "INSERT INTO acceptance_intents VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (milestone_id, *identity, "PREPARED", now, now),
                )
                db.execute(
                    "INSERT INTO journal(milestone_id,event_type,detail,occurred_at) "
                    "VALUES (?,?,?,?)",
                    (milestone_id, "ACCEPTANCE_PREPARED", accepted_sha, now),
                )
                return True

    def pending_acceptances(self) -> list[dict]:
        """Return unresolved intents for verified, owner-authorized recovery."""
        with closing(self._connect()) as db:
            return [
                dict(row) for row in db.execute(
                    "SELECT * FROM acceptance_intents WHERE state='PREPARED' "
                    "ORDER BY created_at,milestone_id"
                )
            ]

    def acceptance_intent(self, milestone_id: str) -> dict | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT * FROM acceptance_intents WHERE milestone_id=?",
                (milestone_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def finish_prepared_acceptance(
        self, *, milestone_id: str, expected_sha: str,
        expected_verification_sha256: str,
    ) -> bool:
        """Atomically finalize accepted milestone, queue and journal in SQLite.

        Caller must have rechecked the exact Git accepted ref, immutable
        milestone ref, native receipt, and owner-control authority.
        """
        with closing(self._connect()) as db:
            with db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT * FROM acceptance_intents WHERE milestone_id=?",
                    (milestone_id,),
                ).fetchone()
                if row is None:
                    raise LocalStateError("ACCEPTANCE_INTENT_MISSING")
                if (
                    row["accepted_sha"] != expected_sha
                    or row["verification_sha256"] != expected_verification_sha256
                ):
                    raise LocalStateError("ACCEPTANCE_INTENT_IDENTITY_CONFLICT")
                if row["state"] == "COMMITTED":
                    milestone = db.execute(
                        "SELECT accepted_sha,verification_sha256 FROM milestones "
                        "WHERE milestone_id=?", (milestone_id,),
                    ).fetchone()
                    if milestone is None or tuple(milestone) != (
                        expected_sha, expected_verification_sha256
                    ):
                        raise LocalStateError("ACCEPTANCE_COMMITTED_STATE_CORRUPT")
                    return False
                now = _utc()
                db.execute(
                    "INSERT INTO milestones VALUES (?,?,?,?,?,?)",
                    (
                        milestone_id, row["repository"], expected_sha,
                        expected_verification_sha256, row["product_reality"], now,
                    ),
                )
                db.execute(
                    "INSERT INTO publications VALUES (?,?,?,?,?)",
                    (milestone_id, row["repository"], "READY", None, now),
                )
                db.execute(
                    "INSERT INTO journal(milestone_id,event_type,detail,occurred_at) "
                    "VALUES (?,?,?,?)",
                    (milestone_id, "MILESTONE_ACCEPTED", expected_sha, now),
                )
                db.execute(
                    "INSERT INTO journal(milestone_id,event_type,detail,occurred_at) "
                    "VALUES (?,?,?,?)",
                    (milestone_id, "PUBLICATION_QUEUED", "READY", now),
                )
                db.execute(
                    "UPDATE acceptance_intents SET state='COMMITTED',changed_at=? "
                    "WHERE milestone_id=? AND state='PREPARED'", (now, milestone_id),
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
            acceptance_intents = [
                dict(row) for row in db.execute(
                    "SELECT * FROM acceptance_intents ORDER BY created_at,milestone_id"
                )
            ]
        return {
            "schema_version": SCHEMA_VERSION,
            "milestones": milestones,
            "publications": publications,
            "journal": journal,
            "acceptance_intents": acceptance_intents,
        }
