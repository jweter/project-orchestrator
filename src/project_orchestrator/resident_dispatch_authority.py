"""Fail-closed, side-effect-free authority preflight for resident dispatch.

This module never creates an authority database, claim, worktree, or worker.
It does not grant candidate-mode authorization or establish accepted Git refs.
Schema admission alone is not a committed acceptance event: the resident
caller must still use the accepted-event selector and durable claim protocol.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Mapping

from project_orchestrator.human_ownership import AUTHORIZED, owner_control_status


class ResidentDispatchBlocked(RuntimeError):
    """The resident dispatcher cannot establish its authority."""


def require_existing_dispatch_authority(
    *,
    controller: Mapping[str, object],
    owner_state_root: Path,
    expected_owner_state_root: Path,
    state_db: Path,
    repo: Path,
) -> None:
    """Validate immutable prerequisites before constructing writable state objects."""
    if owner_control_status(owner_state_root) != AUTHORIZED:
        raise ResidentDispatchBlocked("OWNER_CONTROL_VETO")
    if Path(owner_state_root).resolve() != Path(expected_owner_state_root).resolve():
        raise ResidentDispatchBlocked("OWNER_AUTHORITY_ROOT_MISMATCH")
    if (
        controller.get("mode") != "candidate_mode"
        or controller.get("candidate_execution_authorized") is not True
    ):
        raise ResidentDispatchBlocked("CANDIDATE_EXECUTION_NOT_AUTHORIZED")
    db = Path(state_db)
    if not db.is_file() or db.is_symlink():
        raise ResidentDispatchBlocked("ACCEPTANCE_STATE_DB_MISSING_OR_UNTRUSTED")
    if not Path(repo).is_dir():
        raise ResidentDispatchBlocked("REPOSITORY_NOT_FOUND")
    # mode=ro ensures the probe cannot create or migrate a database.
    try:
        # sqlite3.Connection's context manager commits/rolls back but does
        # not close the handle. Resident probes must release it on all paths.
        with closing(
            sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=1)
        ) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version != 2:
                raise ResidentDispatchBlocked("ACCEPTANCE_STATE_SCHEMA_UNVERIFIED")
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            required = {
                "milestones": {
                    "milestone_id",
                    "repository",
                    "accepted_sha",
                    "verification_sha256",
                    "product_reality",
                    "accepted_at",
                },
                "acceptance_intents": {
                    "milestone_id",
                    "repository",
                    "accepted_base_sha",
                    "accepted_sha",
                    "verification_sha256",
                    "product_reality",
                    "context_json",
                    "state",
                    "created_at",
                    "changed_at",
                },
                "journal": {
                    "event_id",
                    "milestone_id",
                    "event_type",
                    "detail",
                    "occurred_at",
                },
            }
            if not required.keys() <= names:
                raise ResidentDispatchBlocked("ACCEPTANCE_STATE_TABLES_UNVERIFIED")
            # Column names alone are insufficient: a spoofed table with no
            # primary key or NOT NULL constraints is not the authority schema.
            primary_keys = {
                "milestones": ("milestone_id", "TEXT"),
                "acceptance_intents": ("milestone_id", "TEXT"),
                "journal": ("event_id", "INTEGER"),
            }
            for table, columns in required.items():
                info = {
                    row[1]: row
                    for row in connection.execute(f"PRAGMA table_info({table})")
                }
                if not columns <= info.keys():
                    raise ResidentDispatchBlocked("ACCEPTANCE_STATE_SCHEMA_UNVERIFIED")
                key, key_type = primary_keys[table]
                if info[key][2].upper() != key_type or info[key][5] != 1:
                    raise ResidentDispatchBlocked("ACCEPTANCE_STATE_SCHEMA_UNVERIFIED")
                for column in columns - {key}:
                    if info[column][2].upper() != "TEXT" or info[column][3] != 1:
                        raise ResidentDispatchBlocked(
                            "ACCEPTANCE_STATE_SCHEMA_UNVERIFIED"
                        )
            # A structurally plausible schema is not proof that its backing
            # pages and indexes are readable. Never admit a corrupt ledger.
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ResidentDispatchBlocked("ACCEPTANCE_STATE_INTEGRITY_UNVERIFIED")
    except sqlite3.Error as exc:
        raise ResidentDispatchBlocked("ACCEPTANCE_STATE_UNREADABLE") from exc
