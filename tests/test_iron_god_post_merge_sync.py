from __future__ import annotations

import pytest

from project_orchestrator.iron_god_post_merge_sync import (
    PostMergeSyncError,
    synchronize_after_merge,
)
from project_orchestrator.iron_god_publication_job import validate_publication_job


def _job(
    publication_id: str, repository: str, current_state: str, github_base_sha=None
):
    return validate_publication_job(
        dict(
            publication_id=publication_id,
            repository=repository,
            milestone_id=f"M-{publication_id}",
            accepted_sha="a" * 40,
            parent_accepted_sha=None,
            github_base_sha=github_base_sha,
            verification_receipt_ref="receipt-001",
            product_reality_receipt_ref=None,
            required_ci=("build",),
            required_review_policy="one-approval",
            publication_branch=f"iron-god/publication/{publication_id}",
            current_state=current_state,
        )
    )


def test_rejects_job_not_merged():
    merged = _job("PUB-001", "demo-repo", "READY_TO_PUBLISH")
    with pytest.raises(PostMergeSyncError):
        synchronize_after_merge(merged, ())


def test_rebases_queued_jobs_for_same_repository():
    merged = _job("PUB-001", "demo-repo", "MERGED")
    queued = _job(
        "PUB-002", "demo-repo", "READY_TO_PUBLISH", github_base_sha="old" + "0" * 37
    )

    result = synchronize_after_merge(merged, (queued,))

    assert result.new_released_sha == merged.accepted_sha
    assert len(result.rebased_queued_jobs) == 1
    assert result.rebased_queued_jobs[0].github_base_sha == merged.accepted_sha
    assert result.rebased_queued_jobs[0].publication_id == "PUB-002"


def test_excludes_jobs_for_different_repository():
    merged = _job("PUB-001", "demo-repo", "MERGED")
    other_repo_job = _job("PUB-002", "other-repo", "READY_TO_PUBLISH")

    result = synchronize_after_merge(merged, (other_repo_job,))

    assert result.rebased_queued_jobs == ()


def test_input_queued_job_is_not_mutated():
    merged = _job("PUB-001", "demo-repo", "MERGED")
    queued = _job(
        "PUB-002", "demo-repo", "READY_TO_PUBLISH", github_base_sha="old" + "0" * 37
    )

    synchronize_after_merge(merged, (queued,))

    assert queued.github_base_sha == "old" + "0" * 37


def test_empty_queued_jobs_yields_empty_result():
    merged = _job("PUB-001", "demo-repo", "MERGED")
    result = synchronize_after_merge(merged, ())
    assert result.rebased_queued_jobs == ()
