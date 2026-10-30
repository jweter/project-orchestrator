"""IG-044 FIFO publication eligibility under one SQLite writer lease.

Read-only preclaim evidence gate. This module deliberately does NOT issue a
publication claim, start GitHub transport, migrate the schema, or authorize
release. A future fenced claimant must re-run these checks inside the SAME
BEGIN IMMEDIATE transaction used for its conditional transition.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from project_orchestrator.human_ownership import AUTHORIZED, owner_control_status
from project_orchestrator.local_first_state import LocalFirstState
from project_orchestrator.local_git_refs import LocalGitRefs
from project_orchestrator.local_verification import VerificationPolicy
from project_orchestrator.publication_provenance_audit import (
    PublicationProvenanceError,
    inspect_milestone,
)

_ACTIVE = ("PUSHING", "PR_OPEN", "CI_PENDING", "REVIEW_PENDING", "MERGE_READY")
_ELIGIBLE = ("READY", "DEFERRED")


class PublicationEligibilityError(ValueError):
    """A preclaim invariant failed, so publication must not proceed."""


@dataclass(frozen=True)
class PublicationEligibility:
    milestone_id: str
    repository: str
    accepted_sha: str
    previous_state: str
    verification_sha256: str


def verify_oldest_publication_eligibility(
    *,
    state: LocalFirstState,
    refs: LocalGitRefs,
    repo: Path,
    evidence_path: Path,
    policy: VerificationPolicy,
) -> PublicationEligibility | None:
    """Inspect the oldest queued milestone without modifying SQLite or Git.

    The caller supplies the receipt path from trusted local verification
    orchestration, not from an untrusted SQLite context_json field.
    """
    if owner_control_status(refs.owner_state_root) != AUTHORIZED:
        raise PublicationEligibilityError("OWNER_CONTROL_VETO")
    path = Path(state.path)
    if path.is_symlink() or not path.is_file():
        raise PublicationEligibilityError("STATE_DATABASE_UNSAFE")
    db = sqlite3.connect(
        path.resolve().as_uri() + "?mode=rw", uri=True, isolation_level=None, timeout=10
    )
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        # Installed runtime is v2. This is ONLY an inspection step; it
        # must never be mistaken for the v3 token-fenced publication claim.
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version != 2:
            raise PublicationEligibilityError("UNSUPPORTED_INSPECTION_SCHEMA")
        repository = policy.repository
        bad = db.execute(
            "SELECT 1 FROM publications p LEFT JOIN milestones m "
            "ON m.milestone_id=p.milestone_id "
            "WHERE p.repository=? AND p.state<>'RELEASE_SYNCED' AND "
            "(m.milestone_id IS NULL OR m.repository<>p.repository "
            "OR (SELECT COUNT(*) FROM journal j "
            "WHERE j.milestone_id=p.milestone_id "
            "AND j.event_type='MILESTONE_ACCEPTED')<>1 "
            "OR (SELECT COUNT(*) FROM journal j "
            "WHERE j.milestone_id=p.milestone_id "
            "AND j.event_type='MILESTONE_ACCEPTED' "
            "AND j.detail=m.accepted_sha)<>1) LIMIT 1",
            (repository,),
        ).fetchone()
        if bad is not None:
            raise PublicationEligibilityError("PUBLICATION_JOURNAL_UNTRUSTED")
        pending = db.execute(
            "SELECT 1 FROM acceptance_intents WHERE repository=? "
            "AND state='PREPARED' LIMIT 1",
            (repository,),
        ).fetchone()
        if pending is not None:
            return None
        active = db.execute(
            "SELECT 1 FROM publications WHERE repository=? AND state IN "
            "('PUSHING','PR_OPEN','CI_PENDING','REVIEW_PENDING','MERGE_READY') "
            "LIMIT 1",
            (repository,),
        ).fetchone()
        if active is not None:
            return None
        oldest = db.execute(
            "SELECT p.milestone_id,p.repository,p.state,m.accepted_sha,"
            "m.verification_sha256,m.product_reality "
            "FROM publications p JOIN milestones m ON m.milestone_id=p.milestone_id "
            "JOIN journal j ON j.milestone_id=p.milestone_id "
            "AND j.event_type='MILESTONE_ACCEPTED' "
            "WHERE p.repository=? AND p.state<>'RELEASE_SYNCED' "
            "ORDER BY j.event_id LIMIT 1",
            (repository,),
        ).fetchone()
        if oldest is None or oldest["state"] not in _ELIGIBLE:
            return None
        try:
            verified = inspect_milestone(
                repo=repo,
                state=state,
                refs=refs,
                milestone_id=oldest["milestone_id"],
                evidence_path=evidence_path,
                policy=policy,
            )
        except PublicationProvenanceError as exc:
            raise PublicationEligibilityError(f"ACCEPTANCE_NOT_VERIFIED:{exc}") from exc
        if (
            verified.accepted_sha != oldest["accepted_sha"]
            or verified.receipt_sha256 != oldest["verification_sha256"]
            or verified.product_reality != oldest["product_reality"]
        ):
            raise PublicationEligibilityError("VERIFICATION_IDENTITY_CHANGED")
        if owner_control_status(refs.owner_state_root) != AUTHORIZED:
            raise PublicationEligibilityError("OWNER_CONTROL_VETO")
        # The SQLite writer lock keeps all DB state identical until rollback.
        return PublicationEligibility(
            oldest["milestone_id"],
            repository,
            verified.accepted_sha,
            oldest["state"],
            verified.receipt_sha256,
        )
    except sqlite3.DatabaseError as exc:
        raise PublicationEligibilityError("SQLITE_INSPECTION_FAILED") from exc
    finally:
        db.rollback()
        db.close()
