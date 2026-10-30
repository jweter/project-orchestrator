"""Fail-closed crash recovery classification for IG-044 (isolated candidate)."""

from datetime import datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

import pytest

path = (
    Path(__file__).resolve().parents[1]
    / "engineering"
    / "ig044_publication_recovery_candidate.py"
)
spec = spec_from_file_location("ig044_publication_recovery_candidate", path)
mod = module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)
Evidence = mod.PublicationRecoveryEvidence
Decision = mod.RecoveryDecision
plan = mod.plan_publication_recovery


def sample(**updates):
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    data = dict(
        repository="jweter/VoiceStudio",
        accepted_sha="a" * 40,
        publication_state="PUSHING",
        claim_changed_at=now - timedelta(minutes=10),
        observed_at=now - timedelta(seconds=30),
        now=now,
        owner_authorized=True,
        claim_lease_expired_verified=True,
        complete_remote_observation=True,
        branch_sha=None,
        open_pr_head_sha=None,
        open_pr_number=None,
        released_sha_absent_verified=True,
    )
    data.update(updates)
    return Evidence(**data)


def test_fully_proven_no_remote_side_effect_can_defer():
    result = plan(sample())
    assert result.decision == Decision.DEFER
    assert result.next_state == "DEFERRED"


def test_matching_remote_branch_and_pr_can_reattach():
    result = plan(
        sample(
            branch_sha="a" * 40,
            open_pr_head_sha="a" * 40,
            open_pr_number=123,
            released_sha_absent_verified=False,
        )
    )
    assert result.decision == Decision.REATTACH_PR
    assert result.next_state == "PR_OPEN"


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"owner_authorized": False}, "OWNER_CONTROL_VETO"),
        ({"claim_lease_expired_verified": False}, "CLAIM_LEASE_NOT_PROVEN_EXPIRED"),
        ({"repository": "not-a-repository"}, "INVALID_REPOSITORY"),
        ({"accepted_sha": "bad"}, "INVALID_ACCEPTED_SHA"),
        ({"publication_state": "PR_OPEN"}, "NOT_PUSHING"),
        ({"complete_remote_observation": False}, "REMOTE_EVIDENCE_INCOMPLETE"),
        ({"released_sha_absent_verified": False}, "REMOTE_STATE_AMBIGUOUS"),
        ({"branch_sha": "b" * 40}, "REMOTE_STATE_AMBIGUOUS"),
        ({"open_pr_head_sha": "a" * 40, "open_pr_number": 7}, "REMOTE_STATE_AMBIGUOUS"),
        (
            {"branch_sha": "a" * 40, "open_pr_head_sha": "b" * 40, "open_pr_number": 7},
            "REMOTE_STATE_AMBIGUOUS",
        ),
        ({"branch_sha": "invalid"}, "INVALID_BRANCH_SHA"),
        ({"open_pr_head_sha": "invalid", "open_pr_number": 7}, "INVALID_PR_SHA"),
        ({"open_pr_head_sha": "a" * 40}, "PR_IDENTITY_INCOMPLETE"),
        ({"open_pr_number": 7}, "PR_IDENTITY_INCOMPLETE"),
        ({"open_pr_head_sha": "a" * 40, "open_pr_number": 0}, "INVALID_PR_NUMBER"),
        (
            {"claim_changed_at": datetime(2026, 10, 13, tzinfo=timezone.utc)},
            "CLOCK_ROLLBACK",
        ),
        ({"claim_changed_at": datetime(2026, 10, 11)}, "INVALID_TIMESTAMP"),
        (
            {"observed_at": datetime(2026, 10, 11, tzinfo=timezone.utc)},
            "REMOTE_EVIDENCE_STALE",
        ),
        (
            {"observed_at": datetime(2026, 10, 13, tzinfo=timezone.utc)},
            "REMOTE_EVIDENCE_STALE",
        ),
    ],
)
def test_recovery_fail_closed(changes, reason):
    result = plan(sample(**changes))
    assert result.decision == Decision.HOLD
    assert result.reason == reason
    assert result.next_state is None


def test_recent_claim_waits_even_with_complete_absence_evidence():
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    result = plan(sample(claim_changed_at=now - timedelta(seconds=5)))
    assert result.decision == Decision.WAIT


def test_invalid_policy_fails_closed():
    assert plan(sample(), min_stale_seconds=-1).reason == "INVALID_RECOVERY_POLICY"
