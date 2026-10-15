from __future__ import annotations

import pytest

from project_orchestrator.iron_god_merge_safety import decide_safe_to_merge
from project_orchestrator.iron_god_publication_job import validate_publication_job


def _job(current_state: str, github_base_sha: str | None):
    return validate_publication_job(
        dict(
            publication_id="PUB-001",
            repository="demo-repo",
            milestone_id="M-001",
            accepted_sha="a" * 40,
            parent_accepted_sha=None,
            github_base_sha=github_base_sha,
            verification_receipt_ref="receipt-001",
            product_reality_receipt_ref=None,
            required_ci=("build",),
            required_review_policy="one-approval",
            publication_branch="iron-god/publication/PUB-001",
            current_state=current_state,
        )
    )


def test_requires_merge_ready():
    with pytest.raises(ValueError, match="MERGE_READY"):
        decide_safe_to_merge(
            _job("READY_TO_PUBLISH", "b" * 40),
            current_default_branch_sha="b" * 40,
        )


@pytest.mark.parametrize("recorded_base", [None, "", "b" * 39, "b" * 41, "G" * 40])
def test_missing_or_invalid_recorded_base_never_authorizes_merge(recorded_base):
    job = _job("MERGE_READY", recorded_base)
    decision = decide_safe_to_merge(job, current_default_branch_sha="b" * 40)
    assert decision.safe_to_merge is False
    assert decision.resulting_job is job
    assert "recorded GitHub base SHA" in decision.reason


@pytest.mark.parametrize("observed", [None, "", "b" * 39, "z" * 40, "B" * 40])
def test_missing_or_invalid_remote_observation_never_authorizes_merge(observed):
    job = _job("MERGE_READY", "b" * 40)
    decision = decide_safe_to_merge(job, current_default_branch_sha=observed)
    assert decision.safe_to_merge is False
    assert decision.resulting_job is job
    assert "observed remote" in decision.reason


def test_only_exact_matching_40_digit_shas_allow_provisional_merge():
    job = _job("MERGE_READY", "b" * 40)
    decision = decide_safe_to_merge(job, current_default_branch_sha="b" * 40)
    assert decision.safe_to_merge is True
    assert decision.resulting_job is job
    assert "provisional" in decision.reason
    assert "atomic" in decision.reason


def test_mismatched_valid_base_transitions_to_base_diverged():
    job = _job("MERGE_READY", "b" * 40)
    decision = decide_safe_to_merge(job, current_default_branch_sha="c" * 40)
    assert decision.safe_to_merge is False
    assert decision.resulting_job.current_state == "BASE_DIVERGED"
    assert job.current_state == "MERGE_READY"
