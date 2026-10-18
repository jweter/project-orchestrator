"""Idempotent accepted-event -> leased-worker dispatch fence.

Coordinates but does NOT create acceptance authority or an agent runner.
The authoritative acceptance selector validates SQLite+Git+owner+ledger.
One durable event claim is persisted BEFORE the caller may invoke its
already-authorized, independently leased worker. A lost acknowledgement
is UNCERTAIN, never a reason to launch the work again automatically.

Recovery (2026-10-18, owner directive "ENGINE RECOVERY AND AUTHORIZATION
RESOLUTION"): a claim that is conclusively proven to have never reached a
worker may be given a terminal ABORTED_NON_EXECUTED disposition, and only
then may a new, independently fenced attempt be claimed for the same
acceptance event and slice. The original row is never deleted, overwritten
with a different identity, or reused for launch; a replacement attempt is a
new row with its own token, carrying `recovered_from_token` back to the row
it replaces. Non-execution eligibility is assessed by composing the
existing `reconcile_dispatch_outcomes` lease/outcome classifier and the
existing `iron_god_candidate_refs.get_candidate` read -- never by
re-deriving that logic independently here.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .human_ownership import AUTHORIZED, owner_control_status
from .iron_god_candidate_refs import get_candidate
from .local_acceptance_refill import select_next_after_acceptance
from .local_first_state import LocalFirstState
from .local_git_refs import LocalGitRefs

# local_dispatch_outcome_reconciliation imports inspect_claims from this
# module, so that import is deferred into the one function that needs it
# (assess_non_execution_evidence) to avoid a circular import at load time.

_STATES = frozenset({"CLAIMED", "INVOCATION_RESERVED", "RESULT_RECORDED"})
_TERMINAL_NON_EXECUTED = "ABORTED_NON_EXECUTED"

# Only these reconcile_dispatch_outcomes dispositions mean "the lease store
# was actually checked and definitively has no lease for this slice" -- a
# missing lease STORE (WORKER_STORE_MISSING) is deliberately excluded: the
# store's absence could mean "never created" or "evidence lost", and this
# module must fail closed on that ambiguity rather than guess.
_AUTO_RECOVERABLE_DISPOSITIONS = frozenset(
    {"CLAIMED_NOT_INVOKED", "INVOCATION_UNCERTAIN_NO_LEASE"}
)


class DispatchFenceError(RuntimeError):
    """A stale, duplicate, or unverified dispatch attempt must not run."""


@dataclass(frozen=True)
class DispatchClaim:
    event_id: int
    slice_id: str
    accepted_sha: str
    token: str
    state: str


@dataclass(frozen=True)
class NonExecutionEvidence:
    """Read-only verdict on whether a claim provably never reached a worker."""

    eligible: bool
    disposition: str | None
    candidate_commit_sha: str | None
    reasons: tuple[str, ...]


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


def _create_v2_schema(db: sqlite3.Connection) -> None:
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
            attempt INTEGER NOT NULL DEFAULT 1,
            state TEXT NOT NULL
                CHECK (state IN
                    ('CLAIMED','INVOCATION_RESERVED','RESULT_RECORDED','ABORTED_NON_EXECUTED')),
            result_status TEXT,
            recovered_from_token TEXT,
            recovery_authorized_by TEXT,
            recovery_reason TEXT,
            recovery_evidence_json TEXT,
            claimed_utc TEXT NOT NULL,
            updated_utc TEXT NOT NULL,
            PRIMARY KEY (source_db, acceptance_event_id, attempt),
            UNIQUE (source_db, slice_id, attempt),
            UNIQUE (claim_token)
        )
        """
    )
    _ensure_v2_indexes(db)


def _ensure_v2_indexes(db: sqlite3.Connection) -> None:
    # Partial unique indexes are the actual "only one active attempt" fence:
    # a row in the terminal non-executed state is excluded from uniqueness,
    # so a replacement attempt can be inserted once (and only once) the prior
    # row has been given that terminal disposition.
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_active_event "
        "ON acceptance_dispatch_claims(source_db, acceptance_event_id) "
        "WHERE state != 'ABORTED_NON_EXECUTED'"
    )
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_active_slice "
        "ON acceptance_dispatch_claims(source_db, slice_id) "
        "WHERE state != 'ABORTED_NON_EXECUTED'"
    )


def _migrate_v1_to_v2(db: sqlite3.Connection) -> None:
    db.execute(
        "ALTER TABLE acceptance_dispatch_claims RENAME TO acceptance_dispatch_claims_v1"
    )
    _create_v2_schema(db)
    db.execute(
        """
        INSERT INTO acceptance_dispatch_claims
            (source_db, owner_root, acceptance_event_id, milestone_id, repository,
             slice_id, accepted_sha, claim_token, attempt, state, result_status,
             recovered_from_token, recovery_authorized_by, recovery_reason,
             recovery_evidence_json, claimed_utc, updated_utc)
        SELECT source_db, owner_root, acceptance_event_id, milestone_id, repository,
               slice_id, accepted_sha, claim_token, 1, state, result_status,
               NULL, NULL, NULL, NULL, claimed_utc, updated_utc
        FROM acceptance_dispatch_claims_v1
        """
    )
    db.execute("DROP TABLE acceptance_dispatch_claims_v1")


def _initialize(db: sqlite3.Connection, claims_path: Path) -> None:
    """Versioned, backed-up, integrity-checked migration to the current schema.

    Version 0 (fresh DB): create the current schema directly.
    Version 1 (pre-recovery schema): back up the whole database file via
    sqlite3's own online backup API, verify integrity before touching
    anything, migrate inside one atomic transaction (rename -> recreate ->
    copy -> drop), then verify integrity again after commit.
    Version 2 (current): no-op beyond ensuring indexes exist.
    """
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, 1, 2):
        raise DispatchFenceError("DISPATCH_SCHEMA_UNSUPPORTED")
    if version == 1:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise DispatchFenceError(
                "DISPATCH_DB_INTEGRITY_CHECK_FAILED:" + str(integrity)
            )
        backup_path = claims_path.with_name(
            claims_path.name
            + ".pre-migration-v1-backup-"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            + ".sqlite"
        )
        backup_db = sqlite3.connect(backup_path)
        try:
            db.backup(backup_db)
        finally:
            backup_db.close()
    db.execute("BEGIN IMMEDIATE")
    try:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            _create_v2_schema(db)
        elif version == 1:
            _migrate_v1_to_v2(db)
        elif version == 2:
            _ensure_v2_indexes(db)
        else:
            raise DispatchFenceError("DISPATCH_SCHEMA_UNSUPPORTED")
        db.execute("PRAGMA user_version=2")
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    post_integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
    if post_integrity != "ok":
        raise DispatchFenceError(
            "DISPATCH_DB_INTEGRITY_CHECK_FAILED_POST_MIGRATION:" + str(post_integrity)
        )


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

    A duplicate event or slice is held until the existing claim is given a
    terminal disposition (see `recover_non_executed_claim`); there is no
    expiry-based automatic re-execution. Once the existing row for this
    (event, slice) is ABORTED_NON_EXECUTED, this inserts a new attempt row
    rather than reusing or overwriting the old one.
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
        _initialize(db, claims_path)
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
                "AND (acceptance_event_id=? OR slice_id=?) AND state != ?",
                (
                    source,
                    decision.acceptance_event_id,
                    decision.next_slice_id,
                    _TERMINAL_NON_EXECUTED,
                ),
            ).fetchone()
            if duplicated is not None:
                raise DispatchFenceError("EVENT_OR_SLICE_ALREADY_CLAIMED")
            prior = db.execute(
                "SELECT claim_token, attempt FROM acceptance_dispatch_claims "
                "WHERE source_db=? AND (acceptance_event_id=? OR slice_id=?) "
                "AND state=? ORDER BY attempt DESC LIMIT 1",
                (
                    source,
                    decision.acceptance_event_id,
                    decision.next_slice_id,
                    _TERMINAL_NON_EXECUTED,
                ),
            ).fetchone()
            attempt = (prior["attempt"] + 1) if prior is not None else 1
            recovered_from_token = prior["claim_token"] if prior is not None else None
            token = uuid.uuid4().hex
            record = next(
                r
                for r in state.snapshot()["acceptance_intents"]
                if r["milestone_id"] == milestone_id
            )
            moment = _now()
            db.execute(
                "INSERT INTO acceptance_dispatch_claims "
                "(source_db, owner_root, acceptance_event_id, milestone_id, repository, "
                "slice_id, accepted_sha, claim_token, attempt, state, result_status, "
                "recovered_from_token, recovery_authorized_by, recovery_reason, "
                "recovery_evidence_json, claimed_utc, updated_utc) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    source,
                    str(Path(owner_state_root).resolve()),
                    decision.acceptance_event_id,
                    milestone_id,
                    record["repository"],
                    decision.next_slice_id,
                    decision.accepted_sha,
                    token,
                    attempt,
                    "CLAIMED",
                    None,
                    recovered_from_token,
                    None,
                    None,
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


def assess_non_execution_evidence(
    *,
    claim: DispatchClaim,
    state: LocalFirstState,
    refs: LocalGitRefs,
    owner_state_root: Path,
    claims_path: Path,
    worker_db_path: Path,
    repo_root: Path,
) -> NonExecutionEvidence:
    """Read-only. Never writes, resets, or replays anything.

    Composes the existing `reconcile_dispatch_outcomes` lease/outcome
    classifier (rather than re-deriving lease-table logic here) with a
    direct `get_candidate` ref read, so a claim is eligible only when BOTH:
    the reconciler definitively found no lease ever bound to this slice
    (CLAIMED_NOT_INVOKED or INVOCATION_UNCERTAIN_NO_LEASE -- never a bare
    "lease store missing", which is deliberately treated as inconclusive),
    AND no candidate commit ref exists for this slice. Any other finding
    fails closed with a specific reason.
    """
    from .local_dispatch_outcome_reconciliation import (
        OutcomeBridgeError,
        reconcile_dispatch_outcomes,
    )

    reasons: list[str] = []
    try:
        outcomes = reconcile_dispatch_outcomes(
            state=state,
            refs=refs,
            owner_state_root=owner_state_root,
            claims_path=claims_path,
            worker_db_path=worker_db_path,
        )
    except OutcomeBridgeError as exc:
        return NonExecutionEvidence(
            eligible=False,
            disposition=None,
            candidate_commit_sha=None,
            reasons=(f"RECONCILIATION_UNAVAILABLE:{exc}",),
        )
    match = next(
        (
            o
            for o in outcomes
            if o.event_id == claim.event_id and o.slice_id == claim.slice_id
        ),
        None,
    )
    disposition = match.disposition if match is not None else None
    if match is None:
        reasons.append("NO_MATCHING_RECONCILED_CLAIM")
    elif disposition not in _AUTO_RECOVERABLE_DISPOSITIONS:
        reasons.append(f"DISPOSITION_NOT_AUTO_RECOVERABLE:{disposition}")
    candidate_sha = get_candidate(repo_root, claim.slice_id)
    if candidate_sha is not None:
        reasons.append(f"CANDIDATE_COMMIT_EXISTS:{candidate_sha}")
    return NonExecutionEvidence(
        eligible=not reasons,
        disposition=disposition,
        candidate_commit_sha=candidate_sha,
        reasons=tuple(reasons),
    )


def recover_non_executed_claim(
    *,
    claim: DispatchClaim,
    state: LocalFirstState,
    refs: LocalGitRefs,
    owner_state_root: Path,
    claims_path: Path,
    worker_db_path: Path,
    repo_root: Path,
    authorized_by: str,
    reason: str,
) -> DispatchClaim:
    """Give an existing CLAIMED/INVOCATION_RESERVED claim a terminal,
    non-executed disposition -- ONLY when `assess_non_execution_evidence`
    finds it eligible. The original row's identity (event, slice, token,
    accepted_sha) is never changed; only its state and recovery metadata
    columns are updated. This never launches, retries, or reconciles the
    original invocation, and does not by itself authorize a new attempt --
    `claim_accepted_next` separately consults this row's terminal state to
    allow (or not allow) a replacement attempt.

    A concurrent recovery attempt on the same claim is resolved by the
    guarded UPDATE's affected-row count: only one caller's transaction can
    observe the pre-recovery state and successfully flip it.
    """
    _authorized(owner_state_root)
    if not isinstance(authorized_by, str) or not authorized_by.strip():
        raise DispatchFenceError("RECOVERY_AUTHORIZATION_IDENTITY_REQUIRED")
    if not isinstance(reason, str) or not reason.strip():
        raise DispatchFenceError("RECOVERY_REASON_REQUIRED")
    evidence = assess_non_execution_evidence(
        claim=claim,
        state=state,
        refs=refs,
        owner_state_root=owner_state_root,
        claims_path=claims_path,
        worker_db_path=worker_db_path,
        repo_root=repo_root,
    )
    if not evidence.eligible:
        raise DispatchFenceError(
            "NON_EXECUTION_EVIDENCE_INCONCLUSIVE:" + ";".join(evidence.reasons)
        )
    _authorized(owner_state_root)
    db = _connect(claims_path)
    try:
        _initialize(db, claims_path)
        _authorized(owner_state_root)
        db.execute("BEGIN IMMEDIATE")
        try:
            row = db.execute(
                "SELECT * FROM acceptance_dispatch_claims WHERE claim_token=?",
                (claim.token,),
            ).fetchone()
            if (
                row is None
                or row["state"] not in ("CLAIMED", "INVOCATION_RESERVED")
                or row["acceptance_event_id"] != claim.event_id
                or row["slice_id"] != claim.slice_id
                or row["accepted_sha"] != claim.accepted_sha
            ):
                raise DispatchFenceError("STALE_DISPATCH_CLAIM")
            if Path(row["owner_root"]).resolve() != Path(owner_state_root).resolve():
                raise DispatchFenceError("OWNER_ROOT_MISMATCH")
            _authorized(owner_state_root)
            evidence_json = json.dumps(
                {
                    "disposition": evidence.disposition,
                    "candidate_commit_sha": evidence.candidate_commit_sha,
                }
            )
            cur = db.execute(
                "UPDATE acceptance_dispatch_claims SET state=?, result_status=?, "
                "recovery_authorized_by=?, recovery_reason=?, recovery_evidence_json=?, "
                "updated_utc=? WHERE claim_token=? AND state=?",
                (
                    _TERMINAL_NON_EXECUTED,
                    "NON_EXECUTION_VERIFIED",
                    authorized_by.strip(),
                    reason.strip(),
                    evidence_json,
                    _now(),
                    claim.token,
                    row["state"],
                ),
            )
            if cur.rowcount != 1:
                raise DispatchFenceError("RECOVERY_RACE_LOST")
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    finally:
        db.close()
    return DispatchClaim(
        event_id=claim.event_id,
        slice_id=claim.slice_id,
        accepted_sha=claim.accepted_sha,
        token=claim.token,
        state=_TERMINAL_NON_EXECUTED,
    )


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
