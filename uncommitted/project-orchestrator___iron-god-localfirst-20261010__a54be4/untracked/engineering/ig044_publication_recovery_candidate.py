"""IG-044 crash-recovery decision policy (candidate; never mutates Git or SQLite).

A stale PUSHING state is *not* proof that the remote push failed. Recovery
requires a complete, fresh observation of GitHub branch, PR and release state.
The caller must separately enforce owner veto and an atomic claim-token CAS.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import re


class RecoveryDecision(str, Enum):
    HOLD = "HOLD"
    WAIT = "WAIT"
    REATTACH_PR = "REATTACH_PR"
    DEFER = "DEFER"


@dataclass(frozen=True)
class PublicationRecoveryEvidence:
    repository: str
    accepted_sha: str
    publication_state: str
    claim_changed_at: datetime
    observed_at: datetime
    now: datetime
    owner_authorized: bool
    claim_lease_expired_verified: bool
    complete_remote_observation: bool
    branch_sha: str | None
    open_pr_head_sha: str | None
    open_pr_number: int | None
    released_sha_absent_verified: bool


@dataclass(frozen=True)
class RecoveryPlan:
    decision: RecoveryDecision
    reason: str
    expected_state: str = "PUSHING"
    next_state: str | None = None


def plan_publication_recovery(
    evidence: PublicationRecoveryEvidence,
    *,
    min_stale_seconds: int = 120,
    max_observation_age_seconds: int = 300,
) -> RecoveryPlan:
    """Fail closed; does not authorize transitions, transport, or publication."""
    hold = lambda reason: RecoveryPlan(RecoveryDecision.HOLD, reason)
    if not evidence.owner_authorized:
        return hold("OWNER_CONTROL_VETO")
    if not evidence.claim_lease_expired_verified:
        return hold("CLAIM_LEASE_NOT_PROVEN_EXPIRED")
    if (
        not isinstance(evidence.repository, str)
        or re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*",
            evidence.repository,
        )
        is None
    ):
        return hold("INVALID_REPOSITORY")
    if (
        not isinstance(evidence.accepted_sha, str)
        or re.fullmatch(
            r"[a-f0-9]{40}",
            evidence.accepted_sha,
        )
        is None
    ):
        return hold("INVALID_ACCEPTED_SHA")
    if evidence.publication_state != "PUSHING":
        return hold("NOT_PUSHING")
    if min_stale_seconds < 0 or max_observation_age_seconds < 0:
        return hold("INVALID_RECOVERY_POLICY")
    dates = (evidence.claim_changed_at, evidence.observed_at, evidence.now)
    if any(
        not isinstance(x, datetime) or x.tzinfo is None or x.utcoffset() is None
        for x in dates
    ):
        return hold("INVALID_TIMESTAMP")
    claim, observed, now = (x.astimezone(timezone.utc) for x in dates)
    age = (now - claim).total_seconds()
    if age < 0:
        return hold("CLOCK_ROLLBACK")
    if age < min_stale_seconds:
        return RecoveryPlan(RecoveryDecision.WAIT, "CLAIM_NOT_STALE")
    obs_age = (now - observed).total_seconds()
    if obs_age < 0 or obs_age > max_observation_age_seconds:
        return hold("REMOTE_EVIDENCE_STALE")
    if not evidence.complete_remote_observation:
        return hold("REMOTE_EVIDENCE_INCOMPLETE")
    sha = evidence.accepted_sha
    branch, pr_sha, number = (
        evidence.branch_sha,
        evidence.open_pr_head_sha,
        evidence.open_pr_number,
    )
    if branch is not None and not re.fullmatch(r"[a-f0-9]{40}", branch):
        return hold("INVALID_BRANCH_SHA")
    if pr_sha is not None and not re.fullmatch(r"[a-f0-9]{40}", pr_sha):
        return hold("INVALID_PR_SHA")
    if (number is None) != (pr_sha is None):
        return hold("PR_IDENTITY_INCOMPLETE")
    if number is not None and (
        not isinstance(number, int) or isinstance(number, bool) or number < 1
    ):
        return hold("INVALID_PR_NUMBER")
    if pr_sha == sha and branch == sha:
        return RecoveryPlan(
            RecoveryDecision.REATTACH_PR, "MATCHING_OPEN_PR", next_state="PR_OPEN"
        )
    if (
        branch is None
        and pr_sha is None
        and number is None
        and (evidence.released_sha_absent_verified)
    ):
        return RecoveryPlan(
            RecoveryDecision.DEFER, "NO_REMOTE_SIDE_EFFECT", next_state="DEFERRED"
        )
    return hold("REMOTE_STATE_AMBIGUOUS")
