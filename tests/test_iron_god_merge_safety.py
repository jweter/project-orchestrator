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


def test_rejects_job_not_in_merge_ready():
    job = _job("READY_TO_PUBLISH", github_base_sha=None)
    with pytest.raises(ValueError):
        decide_safe_to_merge(job, current_default_branch_sha="b" * 40)


def test_no_recorded_base_sha_is_safe():
    job = _job("MERGE_READY", github_base_sha=None)
    decision = decide_safe_to_merge(job, current_default_branch_sha="b" * 40)
    assert decision.safe_to_merge is True
    assert decision.resulting_job is job


def test_matching_base_sha_is_safe():
    job = _job("MERGE_READY", github_base_sha="b" * 40)
    decision = decide_safe_to_merge(job, current_default_branch_sha="b" * 40)
    assert decision.safe_to_merge is True
    assert decision.resulting_job.current_state == "MERGE_READY"


def test_mismatched_base_sha_is_unsafe_and_transitions_to_base_diverged():
    job = _job("MERGE_READY", github_base_sha="b" * 40)
    decision = decide_safe_to_merge(job, current_default_branch_sha="c" * 40)
    assert decision.safe_to_merge is False
    assert decision.resulting_job.current_state == "BASE_DIVERGED"
