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
import subprocess
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


@dataclass(frozen=True)
class ReviewedNonExecutionAttestation:
    """A human reviewer's explicit, attributed finding -- never inferred.

    `authorization_rejected_before_leasing=True` asserts the reviewer's
    own finding that execution was rejected (e.g. by the owner-control
    gate, the global authorization flag, or a resource check) BEFORE any
    worker lease could have been acquired. `rejection_evidence_reference`
    must point at the concrete record supporting that (a diagnostic
    output, a log line, a session transcript reference) -- this module
    never re-derives that finding on its own, since the failure being
    adjudicated happened in the past and cannot be reconstructed from
    current state alone.
    """

    reviewer_identity: str
    justification: str
    authorization_rejected_before_leasing: bool
    rejection_evidence_reference: str


@dataclass(frozen=True)
class MissingLeaseStoreAdjudication:
    """Read-only verdict for the specific case the automatic
    `assess_non_execution_evidence` deliberately refuses: the lease store
    itself does not exist. Eligibility here requires BOTH an attested
    human finding AND every independently-checkable signal (candidate
    ref absence, accepted-ref stability, the lease store's genuine
    absence) to be consistent with it -- any contradiction is reported
    and makes the claim ineligible."""

    eligible: bool
    reasons: tuple[str, ...]
    acceptance_event_id: int
    claim_token: str
    slice_id: str
    repository: str
    accepted_sha: str
    claimed_utc: str | None
    worker_store_exists: bool
    candidate_commit_sha: str | None
    accepted_ref_matches_claim: bool
    worktree_checked: bool
    worktree_clean: bool | None
    worktree_head_matches_base: bool | None


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


def _write_terminal_non_execution(
    *,
    claim: DispatchClaim,
    claims_path: Path,
    owner_state_root: Path,
    authorized_by: str,
    reason: str,
    evidence_json: str,
) -> DispatchClaim:
    """Shared, atomic write path for BOTH non-execution recovery routes
    (the automatic `recover_non_executed_claim` and the human-reviewed
    `record_reviewed_non_execution_disposition`). Each caller independently
    computes and validates its own eligibility BEFORE calling this; this
    function only ever performs the identity-checked, race-guarded
    CLAIMED/INVOCATION_RESERVED -> ABORTED_NON_EXECUTED transition itself,
    so the two eligibility gates can never drift from two different copies
    of the same SQL.

    A concurrent attempt on the same claim is resolved by the guarded
    UPDATE's affected-row count: only one caller's transaction can observe
    the pre-recovery state and successfully flip it.
    """
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
            cur = db.execute(
                "UPDATE acceptance_dispatch_claims SET state=?, result_status=?, "
                "recovery_authorized_by=?, recovery_reason=?, recovery_evidence_json=?, "
                "updated_utc=? WHERE claim_token=? AND state=?",
                (
                    _TERMINAL_NON_EXECUTED,
                    "NON_EXECUTION_VERIFIED",
                    authorized_by,
                    reason,
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
    finds it eligible (a lease store that was actually checked and found
    definitively empty). The original row's identity (event, slice, token,
    accepted_sha) is never changed; only its state and recovery metadata
    columns are updated. This never launches, retries, or reconciles the
    original invocation, and does not by itself authorize a new attempt --
    `claim_accepted_next` separately consults this row's terminal state to
    allow (or not allow) a replacement attempt.

    A claim whose lease store does not exist AT ALL is never eligible
    through this function -- see `record_reviewed_non_execution_disposition`
    for that deliberately separate, human-reviewed path.
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
    evidence_json = json.dumps(
        {
            "path": "AUTOMATIC_LEASE_STORE_CHECKED_EMPTY",
            "disposition": evidence.disposition,
            "candidate_commit_sha": evidence.candidate_commit_sha,
        }
    )
    return _write_terminal_non_execution(
        claim=claim,
        claims_path=claims_path,
        owner_state_root=owner_state_root,
        authorized_by=authorized_by.strip(),
        reason=reason.strip(),
        evidence_json=evidence_json,
    )


_GIT_ENV_REDIRECT_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CEILING_DIRECTORIES",
)


def _worktree_cleanliness(worktree_path: Path, expected_head: str) -> tuple[bool, bool]:
    """Returns (clean, head_matches_expected). Read-only: status + rev-parse."""
    env = dict(os.environ)
    for key in _GIT_ENV_REDIRECT_VARS:
        env.pop(key, None)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    status = subprocess.run(
        ["git", "-C", str(worktree_path), "status", "--porcelain"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    head = subprocess.run(
        ["git", "-C", str(worktree_path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    clean = status.returncode == 0 and not status.stdout.strip()
    head_matches = head.returncode == 0 and head.stdout.strip() == expected_head
    return clean, head_matches


def adjudicate_missing_lease_store_claim(
    *,
    claim: DispatchClaim,
    refs: LocalGitRefs,
    owner_state_root: Path,
    claims_path: Path,
    worker_db_path: Path,
    repo_root: Path,
    attestation: ReviewedNonExecutionAttestation,
    worktree_path: Path | None = None,
) -> MissingLeaseStoreAdjudication:
    """Read-only. Never writes, resets, or replays anything.

    For the specific case the automatic `assess_non_execution_evidence`
    deliberately refuses: the lease STORE itself does not exist, so its
    absence cannot be distinguished by that classifier from lost or
    deleted evidence. This function never makes that distinction on its
    own either -- it REQUIRES an explicit, attributed human attestation
    that execution was rejected before leasing, and then independently
    cross-checks every OTHER piece of evidence it can read directly: the
    claim row's own identity, whether the lease store genuinely does not
    exist (if it does, this is the wrong function -- see
    `assess_non_execution_evidence`), whether a candidate commit ref
    exists for the slice, whether the accepted Git reference for the
    claim's milestone has moved since the claim was made, and -- when a
    worktree path is supplied and still exists -- whether it is clean and
    at the expected base SHA. Any contradiction between the attestation
    and this independently-read evidence is reported and makes the claim
    ineligible; it is never resolved in the attestation's favor.
    """
    reasons: list[str] = []

    if refs.owner_state_root.resolve() != Path(owner_state_root).resolve():
        reasons.append("OWNER_ROOT_MISMATCH")

    if (
        not isinstance(attestation.reviewer_identity, str)
        or not attestation.reviewer_identity.strip()
    ):
        reasons.append("ATTESTATION_REVIEWER_IDENTITY_REQUIRED")
    if (
        not isinstance(attestation.justification, str)
        or not attestation.justification.strip()
    ):
        reasons.append("ATTESTATION_JUSTIFICATION_REQUIRED")
    if (
        not isinstance(attestation.rejection_evidence_reference, str)
        or not attestation.rejection_evidence_reference.strip()
    ):
        reasons.append("ATTESTATION_REJECTION_EVIDENCE_REFERENCE_REQUIRED")
    if attestation.authorization_rejected_before_leasing is not True:
        reasons.append("ATTESTATION_DOES_NOT_ESTABLISH_REJECTION_BEFORE_LEASING")

    rows = inspect_claims(claims_path)
    row = next((r for r in rows if r["claim_token"] == claim.token), None)
    if (
        row is None
        or row["state"] not in ("CLAIMED", "INVOCATION_RESERVED")
        or row["acceptance_event_id"] != claim.event_id
        or row["slice_id"] != claim.slice_id
        or row["accepted_sha"] != claim.accepted_sha
    ):
        reasons.append("STALE_OR_MISSING_CLAIM_ROW")
        row = row or {}

    worker_store_exists = worker_db_path.is_file()
    if worker_store_exists:
        reasons.append("WORKER_STORE_EXISTS_USE_AUTOMATIC_RECOVERY_PATH")

    candidate_sha = get_candidate(repo_root, claim.slice_id)
    if candidate_sha is not None:
        reasons.append(f"CANDIDATE_COMMIT_EXISTS:{candidate_sha}")

    milestone_id = row.get("milestone_id")
    accepted_ref_matches = False
    if milestone_id:
        accepted_ref_matches = (
            refs.accepted() == claim.accepted_sha
            and refs.get("milestone", milestone_id) == claim.accepted_sha
        )
        if not accepted_ref_matches:
            reasons.append("ACCEPTED_REF_MOVED_SINCE_CLAIM")
    else:
        reasons.append("MILESTONE_ID_UNAVAILABLE_FOR_REF_CHECK")

    worktree_checked = False
    worktree_clean: bool | None = None
    worktree_head_matches: bool | None = None
    if worktree_path is not None and worktree_path.is_dir():
        worktree_checked = True
        worktree_clean, worktree_head_matches = _worktree_cleanliness(
            worktree_path, claim.accepted_sha
        )
        if not worktree_clean or not worktree_head_matches:
            reasons.append("WORKTREE_NOT_CLEAN_OR_HEAD_MISMATCH")

    return MissingLeaseStoreAdjudication(
        eligible=not reasons,
        reasons=tuple(reasons),
        acceptance_event_id=claim.event_id,
        claim_token=claim.token,
        slice_id=claim.slice_id,
        repository=row.get("repository", ""),
        accepted_sha=claim.accepted_sha,
        claimed_utc=row.get("claimed_utc"),
        worker_store_exists=worker_store_exists,
        candidate_commit_sha=candidate_sha,
        accepted_ref_matches_claim=accepted_ref_matches,
        worktree_checked=worktree_checked,
        worktree_clean=worktree_clean,
        worktree_head_matches_base=worktree_head_matches,
    )


def record_reviewed_non_execution_disposition(
    *,
    claim: DispatchClaim,
    refs: LocalGitRefs,
    owner_state_root: Path,
    claims_path: Path,
    worker_db_path: Path,
    repo_root: Path,
    attestation: ReviewedNonExecutionAttestation,
    worktree_path: Path | None = None,
) -> DispatchClaim:
    """Apply a human-reviewed non-execution disposition to a claim whose
    lease store does not exist at all -- ONLY when
    `adjudicate_missing_lease_store_claim` finds it eligible. Shares the
    same atomic, race-guarded write as the automatic recovery path (see
    `_write_terminal_non_execution`) so the two routes can never diverge
    in how they actually mutate the claims database, only in how each
    independently decides whether to call it.
    """
    _authorized(owner_state_root)
    adjudication = adjudicate_missing_lease_store_claim(
        claim=claim,
        refs=refs,
        owner_state_root=owner_state_root,
        claims_path=claims_path,
        worker_db_path=worker_db_path,
        repo_root=repo_root,
        attestation=attestation,
        worktree_path=worktree_path,
    )
    if not adjudication.eligible:
        raise DispatchFenceError(
            "REVIEWED_NON_EXECUTION_EVIDENCE_INCONCLUSIVE:"
            + ";".join(adjudication.reasons)
        )
    evidence_json = json.dumps(
        {
            "path": "HUMAN_REVIEWED_MISSING_STORE_ADJUDICATION",
            "rejection_evidence_reference": attestation.rejection_evidence_reference,
            "candidate_commit_sha": adjudication.candidate_commit_sha,
            "worker_store_exists": adjudication.worker_store_exists,
            "accepted_ref_matches_claim": adjudication.accepted_ref_matches_claim,
            "worktree_checked": adjudication.worktree_checked,
        }
    )
    return _write_terminal_non_execution(
        claim=claim,
        claims_path=claims_path,
        owner_state_root=owner_state_root,
        authorized_by=attestation.reviewer_identity.strip(),
        reason=attestation.justification.strip(),
        evidence_json=evidence_json,
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
