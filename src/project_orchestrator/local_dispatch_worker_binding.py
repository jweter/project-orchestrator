"""Bind one durable dispatch claim to one already-fenced local worker lease.

Additive SQLite table in the *existing worker lease database*; no lease-schema
replacement, worker execution, acceptance, Git mutation, or authority change.
Only a live, owner-authorized, matching invocation may bind an active lease.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .human_ownership import AUTHORIZED, owner_control_status
from .local_dispatch_event_claim import DispatchClaim, inspect_claims
from .local_first_state import LocalFirstState
from .local_git_refs import LocalGitRefs

_ATTEMPT = re.compile(r"[0-9a-f]{32}\Z")


class WorkerBindingError(RuntimeError):
    """Refuse unproven dispatch-to-worker identity."""


@dataclass(frozen=True)
class WorkerBinding:
    slice_id: str
    claim_token: str
    attempt_id: str
    generation: int
    accepted_base_sha: str


def _owner(root: Path) -> None:
    if owner_control_status(root) != AUTHORIZED:
        raise WorkerBindingError("OWNER_CONTROL_VETO")


def _admission(
    *,
    claim: DispatchClaim,
    claims_path: Path,
    state: LocalFirstState,
    refs: LocalGitRefs,
    owner_state_root: Path,
) -> dict:
    _owner(owner_state_root)
    if refs.owner_state_root.resolve() != owner_state_root.resolve():
        raise WorkerBindingError("OWNER_ROOT_MISMATCH")
    source = os.path.normcase(str(state.path.resolve()))
    matches = [
        row for row in inspect_claims(claims_path) if row["claim_token"] == claim.token
    ]
    if len(matches) != 1:
        raise WorkerBindingError("DISPATCH_CLAIM_MISSING")
    row = matches[0]
    if (
        row["source_db"] != source
        or Path(row["owner_root"]).resolve() != owner_state_root.resolve()
        or row["state"] != "INVOCATION_RESERVED"
        or row["acceptance_event_id"] != claim.event_id
        or row["slice_id"] != claim.slice_id
        or row["accepted_sha"] != claim.accepted_sha
        or claim.state != "CLAIMED"
    ):
        raise WorkerBindingError("DISPATCH_CLAIM_IDENTITY_UNVERIFIED")
    snapshot = state.snapshot()
    intent = next(
        (
            r
            for r in snapshot["acceptance_intents"]
            if r["milestone_id"] == row["milestone_id"]
        ),
        None,
    )
    milestone = next(
        (r for r in snapshot["milestones"] if r["milestone_id"] == row["milestone_id"]),
        None,
    )
    event = next(
        (r for r in snapshot["journal"] if r["event_id"] == claim.event_id), None
    )
    if (
        intent is None
        or intent["state"] != "COMMITTED"
        or milestone is None
        or intent["accepted_sha"] != claim.accepted_sha
        or milestone["accepted_sha"] != claim.accepted_sha
        or intent["repository"] != row["repository"]
        or milestone["repository"] != row["repository"]
        or milestone["verification_sha256"] != intent["verification_sha256"]
        or event is None
        or event["milestone_id"] != row["milestone_id"]
        or event["event_type"] != "MILESTONE_ACCEPTED"
        or event["detail"] != claim.accepted_sha
        or refs.accepted() != claim.accepted_sha
        or refs.get("milestone", row["milestone_id"]) != claim.accepted_sha
    ):
        raise WorkerBindingError("ACCEPTED_BASE_UNVERIFIED")
    return row


def bind_worker_lease(
    *,
    claim: DispatchClaim,
    claims_path: Path,
    state: LocalFirstState,
    refs: LocalGitRefs,
    owner_state_root: Path,
    worker_db_path: Path,
    attempt_id: str,
    generation: int,
    now: float | None = None,
) -> WorkerBinding:
    """Bind while the worker holds the current lease, BEFORE its first outcome.

    Existing production worker still controls acquisition/fencing of the
    lease. No stale or completed lease can acquire a dispatch provenance row.
    Idempotent identical binds are allowed; conflicting token, attempt, or
    generation are fenced. A crash after claim but before this call remains
    uncertain and must not trigger automatic replay.
    """
    if (
        not isinstance(attempt_id, str)
        or not _ATTEMPT.fullmatch(attempt_id)
        or type(generation) is not int
        or generation < 1
    ):
        raise WorkerBindingError("WORKER_ATTEMPT_IDENTITY_INVALID")
    row = _admission(
        claim=claim,
        claims_path=claims_path,
        state=state,
        refs=refs,
        owner_state_root=owner_state_root,
    )
    if not worker_db_path.is_file() or worker_db_path.is_symlink():
        raise WorkerBindingError("WORKER_LEASE_DB_MISSING")
    if worker_db_path.resolve() in {claims_path.resolve(), state.path.resolve()}:
        raise WorkerBindingError("WORKER_DB_IDENTITY_CONFLICT")
    observed_at = time.time() if now is None else now
    db = sqlite3.connect(worker_db_path, isolation_level=None, timeout=15)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("PRAGMA synchronous=FULL")
        if db.execute("PRAGMA user_version").fetchone()[0] != 4:
            raise WorkerBindingError("WORKER_SCHEMA_UNSUPPORTED")
        db.execute("BEGIN IMMEDIATE")
        try:
            # Additive table; existing worker slice_leases and slice_outcomes
            # remain unchanged, so all existing fences keep working.
            db.execute(
                """CREATE TABLE IF NOT EXISTS dispatch_attempt_bindings (
                    slice_id TEXT PRIMARY KEY,
                    claim_token TEXT UNIQUE NOT NULL,
                    source_db TEXT NOT NULL,
                    owner_root TEXT NOT NULL,
                    acceptance_event_id INTEGER NOT NULL,
                    accepted_base_sha TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    bound_at REAL NOT NULL
                )"""
            )
            lease = db.execute(
                "SELECT * FROM slice_leases WHERE slice_id=?",
                (claim.slice_id,),
            ).fetchone()
            if (
                lease is None
                or lease["attempt_id"] != attempt_id
                or lease["generation"] != generation
                or lease["base_sha"] != claim.accepted_sha
                or lease["state"] not in ("LEASED", "BUILDING", "VERIFYING")
                or not isinstance(lease["lease_until"], (int, float))
                or lease["lease_until"] <= observed_at
            ):
                raise WorkerBindingError("ACTIVE_WORKER_LEASE_UNVERIFIED")
            starts = db.execute(
                "SELECT * FROM slice_lease_events "
                "WHERE slice_id=? AND event_type='SLICE_LEASED'",
                (claim.slice_id,),
            ).fetchall()
            if (
                len(starts) != 1
                or starts[0]["attempt_id"] != attempt_id
                or starts[0]["generation"] != generation
                or starts[0]["detail"] != claim.accepted_sha
                or not isinstance(starts[0]["occurred_at"], (float, int))
                or starts[0]["occurred_at"]
                < datetime.fromisoformat(row["claimed_utc"]).timestamp()
            ):
                raise WorkerBindingError("WORKER_LEASE_EVENT_CONFLICT")
            if db.execute(
                "SELECT 1 FROM slice_outcomes WHERE slice_id=?",
                (claim.slice_id,),
            ).fetchone():
                raise WorkerBindingError("WORKER_OUTCOME_ALREADY_RECORDED")

            identity = (
                claim.slice_id,
                claim.token,
                row["source_db"],
                str(owner_state_root.resolve()),
                claim.event_id,
                claim.accepted_sha,
                attempt_id,
                generation,
            )
            previous = db.execute(
                "SELECT * FROM dispatch_attempt_bindings "
                "WHERE slice_id=? OR claim_token=?",
                (claim.slice_id, claim.token),
            ).fetchall()
            if previous:
                if len(previous) != 1 or tuple(previous[0][:8]) != identity:
                    raise WorkerBindingError("WORKER_BINDING_CONFLICT")
            else:
                db.execute(
                    "INSERT INTO dispatch_attempt_bindings VALUES (?,?,?,?,?,?,?,?,?)",
                    (*identity, observed_at),
                )
            _owner(owner_state_root)
            # Recheck accepted refs and dispatch claim while holding the
            # worker DB write lock; cross-DB commits aren't atomic.
            if refs.accepted() != claim.accepted_sha:
                raise WorkerBindingError("ACCEPTED_BASE_CHANGED")
            reread = [
                r
                for r in inspect_claims(claims_path)
                if r["claim_token"] == claim.token
            ]
            if len(reread) != 1 or reread[0]["state"] != "INVOCATION_RESERVED":
                raise WorkerBindingError("DISPATCH_CLAIM_STATE_CHANGED")
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        return WorkerBinding(
            slice_id=claim.slice_id,
            claim_token=claim.token,
            attempt_id=attempt_id,
            generation=generation,
            accepted_base_sha=claim.accepted_sha,
        )
    finally:
        db.close()
