"""Idempotent accepted-event -> leased-worker dispatch fence.

Coordinates but does NOT create acceptance authority or an agent runner.
The authoritative acceptance selector validates SQLite+Git+owner+ledger.
One durable event claim is persisted BEFORE the caller may invoke its
already-authorized, independently leased worker. A lost acknowledgement
is UNCERTAIN, never a reason to launch the work again automatically.
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .human_ownership import AUTHORIZED, owner_control_status
from .local_acceptance_refill import select_next_after_acceptance
from .local_first_state import LocalFirstState
from .local_git_refs import LocalGitRefs

_STATES = frozenset({"CLAIMED", "INVOCATION_RESERVED", "RESULT_RECORDED"})


class DispatchFenceError(RuntimeError):
    """A stale, duplicate, or unverified dispatch attempt must not run."""


@dataclass(frozen=True)
class DispatchClaim:
    event_id: int
    slice_id: str
    accepted_sha: str
    token: str
    state: str


def _store_identity(state: LocalFirstState) -> str:
    return os.path.normcase(str(state.path.resolve()))


def _connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=15, isolation_level=None)
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        # Two first-time contenders may race while switching a new DB to WAL.
        # Retry only that transient lock; claim uniqueness remains transactional.
        deadline = time.monotonic() + 15
        while True:
            try:
                db.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        db.execute("PRAGMA synchronous=FULL")
        return db
    except BaseException:
        db.close()
        raise


def _initialize(db: sqlite3.Connection) -> None:
    db.execute("BEGIN IMMEDIATE")
    try:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise DispatchFenceError("DISPATCH_SCHEMA_UNSUPPORTED")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS acceptance_dispatch_claims (
                source_db TEXT NOT NULL,
                owner_root TEXT NOT NULL,
                acceptance_event_id INTEGER NOT NULL,
                milestone_id TEXT NOT NULL,
                repository TEXT NOT NULL,
                slice_id TEXT NOT NULL,
                accepted_sha TEXT NOT NULL,
                claim_token TEXT NOT NULL,
                state TEXT NOT NULL
                    CHECK (state IN ('CLAIMED','INVOCATION_RESERVED','RESULT_RECORDED')),
                result_status TEXT,
                claimed_utc TEXT NOT NULL,
                updated_utc TEXT NOT NULL,
                PRIMARY KEY (source_db, acceptance_event_id),
                UNIQUE (source_db, slice_id),
                UNIQUE (claim_token)
            )
            """
        )
        db.execute("PRAGMA user_version=1")
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _authorized(root: Path) -> None:
    if owner_control_status(root) != AUTHORIZED:
        raise DispatchFenceError("OWNER_CONTROL_VETO")


def _selected(
    *,
    state: LocalFirstState,
    refs: LocalGitRefs,
    owner_state_root: Path,
    milestone_id: str,
    controller: Mapping[str, Any],
    handoffs: Mapping[str, Mapping[str, Any]],
    active_slice_ids: tuple[str, ...],
):
    decision = select_next_after_acceptance(
        state=state,
        refs=refs,
        owner_state_root=owner_state_root,
        milestone_id=milestone_id,
        controller=controller,
        handoffs=handoffs,
        active_slice_ids=active_slice_ids,
    )
    if decision.status != "READY_FOR_LEASED_DISPATCH":
        raise DispatchFenceError("SELECTOR_NOT_READY:" + decision.reason)
    if not isinstance(decision.acceptance_event_id, int) or (
        decision.acceptance_event_id <= 0
        or not decision.next_slice_id
        or not decision.accepted_sha
    ):
        raise DispatchFenceError("SELECTOR_RESULT_UNVERIFIED")
    return decision


def claim_accepted_next(
    *,
    state: LocalFirstState,
    refs: LocalGitRefs,
    owner_state_root: Path,
    milestone_id: str,
    controller: Mapping[str, Any],
    handoffs: Mapping[str, Mapping[str, Any]],
    claims_path: Path,
    active_slice_ids: tuple[str, ...] = (),
) -> DispatchClaim:
    """Atomically and durably claim one accepted event for one next slice.

    Duplicate event or slice is permanently held until human-reviewed
    reconciliation; no expiry-based automatic re-execution is permitted.
    """
    _authorized(owner_state_root)
    decision = _selected(
        state=state,
        refs=refs,
        owner_state_root=owner_state_root,
        milestone_id=milestone_id,
        controller=controller,
        handoffs=handoffs,
        active_slice_ids=active_slice_ids,
    )
    if refs.owner_state_root.resolve() != Path(owner_state_root).resolve():
        raise DispatchFenceError("OWNER_ROOT_MISMATCH")
    # The caller provides this separate store. Do not overwrite acceptance DB.
    if Path(claims_path).resolve() == state.path.resolve():
        raise DispatchFenceError("DISPATCH_DB_MUST_BE_SEPARATE")
    _authorized(owner_state_root)
    claims_path.parent.mkdir(parents=True, exist_ok=True)
    db = _connect(claims_path)
    try:
        _authorized(owner_state_root)
        _initialize(db)
        db.execute("BEGIN IMMEDIATE")
        try:
            # Revalidate against the real acceptance source and refs while
            # owning the dispatch transaction, before inserting claim.
            second = _selected(
                state=state,
                refs=refs,
                owner_state_root=owner_state_root,
                milestone_id=milestone_id,
                controller=controller,
                handoffs=handoffs,
                active_slice_ids=active_slice_ids,
            )
            if second != decision:
                raise DispatchFenceError("SELECTION_CHANGED")
            _authorized(owner_state_root)
            source = _store_identity(state)
            duplicated = db.execute(
                "SELECT 1 FROM acceptance_dispatch_claims WHERE source_db=? "
                "AND (acceptance_event_id=? OR slice_id=?)",
                (source, decision.acceptance_event_id, decision.next_slice_id),
            ).fetchone()
            if duplicated is not None:
                raise DispatchFenceError("EVENT_OR_SLICE_ALREADY_CLAIMED")
            token = uuid.uuid4().hex
            record = next(
                r
                for r in state.snapshot()["acceptance_intents"]
                if r["milestone_id"] == milestone_id
            )
            moment = _now()
            db.execute(
                "INSERT INTO acceptance_dispatch_claims VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    source,
                    str(Path(owner_state_root).resolve()),
                    decision.acceptance_event_id,
                    milestone_id,
                    record["repository"],
                    decision.next_slice_id,
                    decision.accepted_sha,
                    token,
                    "CLAIMED",
                    None,
                    moment,
                    moment,
                ),
            )
            _authorized(owner_state_root)
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        return DispatchClaim(
            event_id=decision.acceptance_event_id,
            slice_id=decision.next_slice_id,
            accepted_sha=decision.accepted_sha,
            token=token,
            state="CLAIMED",
        )
    finally:
        db.close()


def _transition(
    *,
    claim: DispatchClaim,
    claims_path: Path,
    owner_state_root: Path,
    expected: str,
    target: str,
    status: str | None = None,
) -> None:
    _authorized(owner_state_root)
    if target not in _STATES or expected not in _STATES:
        raise DispatchFenceError("INVALID_DISPATCH_TRANSITION")
    db = _connect(claims_path)
    try:
        db.execute("BEGIN IMMEDIATE")
        try:
            row = db.execute(
                "SELECT * FROM acceptance_dispatch_claims WHERE claim_token=?",
                (claim.token,),
            ).fetchone()
            if (
                row is None
                or row["state"] != expected
                or row["acceptance_event_id"] != claim.event_id
                or row["slice_id"] != claim.slice_id
                or row["accepted_sha"] != claim.accepted_sha
            ):
                raise DispatchFenceError("STALE_DISPATCH_CLAIM")
            if Path(row["owner_root"]).resolve() != Path(owner_state_root).resolve():
                raise DispatchFenceError("OWNER_ROOT_MISMATCH")
            _authorized(owner_state_root)
            db.execute(
                "UPDATE acceptance_dispatch_claims "
                "SET state=?,result_status=?,updated_utc=? "
                "WHERE claim_token=? AND state=?",
                (target, status, _now(), claim.token, expected),
            )
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    finally:
        db.close()


def invoke_once(
    *,
    claim: DispatchClaim,
    claims_path: Path,
    owner_state_root: Path,
    revalidate: Callable[[], bool],
    executor: Callable[[DispatchClaim], Any],
) -> Any:
    """Reserve before callback, so crashes cannot cause a duplicate launch.

    The executor must independently claim the existing fenced SLICE lease.
    If callback fails or owner takes over, remain INVOCATION_RESERVED (uncertain).
    Never re-run this claim automatically. Callers must reconcile its outcome.
    """
    _authorized(owner_state_root)
    if claim.state != "CLAIMED" or revalidate() is not True:
        raise DispatchFenceError("PRE_INVOKE_AUTHORITY_UNVERIFIED")
    _transition(
        claim=claim,
        claims_path=claims_path,
        owner_state_root=owner_state_root,
        expected="CLAIMED",
        target="INVOCATION_RESERVED",
    )
    # This final pre-invoke check is essential when owner control changes
    # between the durable reservation and the actual builder call.
    _authorized(owner_state_root)
    if revalidate() is not True:
        raise DispatchFenceError("PRE_INVOKE_AUTHORITY_REVOKED")
    result = executor(claim)
    # An executor exception or owner takeover leaves an uncertain durable
    # reservation; never guess whether Git/build side-effects occurred.
    _transition(
        claim=claim,
        claims_path=claims_path,
        owner_state_root=owner_state_root,
        expected="INVOCATION_RESERVED",
        target="RESULT_RECORDED",
        status="EXECUTOR_RETURNED_UNVERIFIED",
    )
    return result


def inspect_claims(claims_path: Path) -> list[dict[str, Any]]:
    """Read-only crash-recovery inventory. Never executes or resets claims."""
    if not claims_path.is_file():
        return []
    db = sqlite3.connect(f"file:{claims_path.as_posix()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in db.execute(
                "SELECT * FROM acceptance_dispatch_claims "
                "ORDER BY claimed_utc,acceptance_event_id"
            )
        ]
    finally:
        db.close()
