"""IG-014: narrow read-only structural health inspection for the v2 local-first
SQLite state database.

This is a diagnostic probe, not an authority. It never creates, migrates,
repairs, writes to, or checkpoints the database; it opens the existing file
through a SQLite read-only URI connection and sets PRAGMA query_only as a
second, independent guard against any write. It fails closed on anything it
cannot positively confirm, and only ever accepts the exact installed schema
version produced by LocalFirstState today.

A STRUCTURALLY_CONSISTENT result means the rows in this database are
internally coherent with each other. It is NOT proof that the referenced Git
commits are immutable or reachable, that any native preflight/verification
receipt is genuine, or that Product Reality was actually cleared. Those facts
live outside SQLite and must be independently reverified by a controller that
holds owner-control authority before any acceptance or publication action.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from project_orchestrator.local_first_state import (
    ACTIVE_PUBLICATIONS,
    SCHEMA_VERSION,
    TRANSITIONS,
)

STRUCTURALLY_CONSISTENT = "STRUCTURALLY_CONSISTENT"

REQUIRED_TABLES = frozenset(
    {"milestones", "publications", "journal", "acceptance_intents"}
)
_VALID_PUBLICATION_STATES = frozenset(TRANSITIONS)
_VALID_INTENT_STATES = frozenset({"PREPARED", "COMMITTED"})

_SCOPE_NOTE = (
    "Structural consistency only. This tool cannot independently prove "
    "immutable Git refs, native preflight provenance, or Product Reality, "
    "and it is not an acceptance or publication authority."
)


def inspect_structural_health(db_path: Path) -> dict:
    """Read-only, fail-closed structural check of an existing state database.

    Always returns a result dict with a "status" key; never raises for an
    expected condition (absent/symlinked/corrupt file, wrong schema version,
    missing tables, or an internally inconsistent row set). Unanticipated
    schema or read failures are reported as BLOCKED_* rather than raised.
    """
    path = Path(db_path)
    if path.is_symlink():
        return {"status": "DATABASE_PATH_IS_SYMLINK", "path": str(path)}
    if not path.exists():
        return {"status": "DATABASE_ABSENT", "path": str(path)}
    if not path.is_file():
        return {"status": "DATABASE_PATH_NOT_A_FILE", "path": str(path)}

    try:
        uri = path.resolve().as_uri() + "?mode=ro"
        db = sqlite3.connect(uri, uri=True)
    except (sqlite3.Error, OSError, ValueError) as exc:
        return {"status": "BLOCKED_DATABASE_UNOPENABLE", "error": repr(exc)}

    db.row_factory = sqlite3.Row
    try:
        with closing(db):
            db.execute("PRAGMA query_only = ON")
            try:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            except sqlite3.DatabaseError as exc:
                return {"status": "DATABASE_CORRUPT", "error": str(exc)}
            if integrity != "ok":
                return {"status": "DATABASE_CORRUPT", "detail": integrity}
            if version != SCHEMA_VERSION:
                return {
                    "status": "UNSUPPORTED_SCHEMA_VERSION",
                    "schema_version": version,
                }
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            missing = sorted(REQUIRED_TABLES - tables)
            if missing:
                return {"status": "REQUIRED_TABLE_MISSING", "missing_tables": missing}
            return _inspect_rows(db)
    except sqlite3.Error as exc:
        return {"status": "BLOCKED_UNEXPECTED_DATABASE_ERROR", "error": repr(exc)}
    except OSError as exc:
        return {"status": "BLOCKED_UNEXPECTED_IO_ERROR", "error": repr(exc)}


def _inspect_rows(db: sqlite3.Connection) -> dict:
    milestones = {
        row["milestone_id"]: dict(row)
        for row in db.execute(
            "SELECT milestone_id, repository, accepted_sha, verification_sha256, "
            "product_reality, accepted_at FROM milestones"
        )
    }
    publications = {
        row["milestone_id"]: dict(row)
        for row in db.execute(
            "SELECT milestone_id, repository, state, last_error, changed_at "
            "FROM publications"
        )
    }

    for row in publications.values():
        if row["state"] not in _VALID_PUBLICATION_STATES:
            return {
                "status": "INVALID_PUBLICATION_STATE",
                "milestone_id": row["milestone_id"],
                "state": row["state"],
            }

    orphan_publications = sorted(set(publications) - set(milestones))
    if orphan_publications:
        return {"status": "ORPHAN_PUBLICATION", "milestone_ids": orphan_publications}
    orphan_milestones = sorted(set(milestones) - set(publications))
    if orphan_milestones:
        return {"status": "ORPHAN_MILESTONE", "milestone_ids": orphan_milestones}

    active_by_repo: dict[str, list[str]] = {}
    for row in publications.values():
        if row["state"] in ACTIVE_PUBLICATIONS:
            active_by_repo.setdefault(row["repository"], []).append(row["milestone_id"])
    for repository, ids in active_by_repo.items():
        if len(ids) > 1:
            return {
                "status": "DUPLICATE_ACTIVE_PUBLICATION",
                "repository": repository,
                "milestone_ids": sorted(ids),
            }

    intents = [
        dict(row)
        for row in db.execute(
            "SELECT milestone_id, repository, accepted_sha, verification_sha256, "
            "state FROM acceptance_intents"
        )
    ]
    prepared_by_repo: dict[str, list[str]] = {}
    for intent in intents:
        if intent["state"] not in _VALID_INTENT_STATES:
            return {
                "status": "INVALID_ACCEPTANCE_INTENT_STATE",
                "milestone_id": intent["milestone_id"],
                "state": intent["state"],
            }
        if intent["state"] == "PREPARED":
            prepared_by_repo.setdefault(intent["repository"], []).append(
                intent["milestone_id"]
            )
            if intent["milestone_id"] in milestones:
                return {
                    "status": "ACCEPTANCE_INTENT_CONTRADICTS_MILESTONE",
                    "milestone_id": intent["milestone_id"],
                }
        else:
            milestone = milestones.get(intent["milestone_id"])
            if milestone is None or (
                milestone["accepted_sha"] != intent["accepted_sha"]
                or milestone["verification_sha256"] != intent["verification_sha256"]
            ):
                return {
                    "status": "ACCEPTANCE_INTENT_MISMATCH",
                    "milestone_id": intent["milestone_id"],
                }
    for repository, ids in prepared_by_repo.items():
        if len(ids) > 1:
            return {
                "status": "DUPLICATE_PREPARED_ACCEPTANCE",
                "repository": repository,
                "milestone_ids": sorted(ids),
            }

    accepted_journal: dict[str, list[str]] = {}
    for row in db.execute(
        "SELECT milestone_id, detail FROM journal WHERE event_type='MILESTONE_ACCEPTED'"
    ):
        accepted_journal.setdefault(row["milestone_id"], []).append(row["detail"])
    for milestone_id, milestone in milestones.items():
        details = accepted_journal.get(milestone_id, [])
        if not details:
            return {
                "status": "MILESTONE_ACCEPTED_JOURNAL_MISSING",
                "milestone_id": milestone_id,
            }
        if len(details) > 1:
            return {
                "status": "MILESTONE_ACCEPTED_JOURNAL_DUPLICATED",
                "milestone_id": milestone_id,
                "count": len(details),
            }
        if details[0] != milestone["accepted_sha"]:
            return {
                "status": "MILESTONE_ACCEPTED_JOURNAL_MISMATCHED",
                "milestone_id": milestone_id,
            }

    return {
        "status": STRUCTURALLY_CONSISTENT,
        "schema_version": SCHEMA_VERSION,
        "milestone_count": len(milestones),
        "pending_acceptance_count": sum(len(v) for v in prepared_by_repo.values()),
        "active_publication_count": sum(len(v) for v in active_by_repo.values()),
        "note": _SCOPE_NOTE,
    }
