from __future__ import annotations

import pytest

from project_orchestrator.iron_god_post_merge_sync import (
    PostMergeSyncError,
    synchronize_after_merge,
)
from project_orchestrator.iron_god_publication_job import validate_publication_job


REMOTE_MERGE_SHA = "f" * 40
LOCAL_ACCEPTED_SHA = "a" * 40


def _job(publication_id, repository, current_state, github_base_sha=None):
    return validate_publication_job(
        dict(
            publication_id=publication_id,
            repository=repository,
            milestone_id=f"M-{publication_id}",
            accepted_sha=LOCAL_ACCEPTED_SHA,
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
        synchronize_after_merge(merged, (), observed_remote_merged_sha=REMOTE_MERGE_SHA)


def test_remote_sha_is_required():
    merged = _job("PUB-001", "demo-repo", "MERGED")
    with pytest.raises(TypeError):
        synchronize_after_merge(merged, ())


@pytest.mark.parametrize("remote", [None, "", "z" * 40, "A" * 40, "f" * 39])
def test_invalid_remote_sha_fails_closed(remote):
    merged = _job("PUB-001", "demo-repo", "MERGED")
    with pytest.raises(PostMergeSyncError, match="REMOTE_MERGED_SHA_UNVERIFIED"):
        synchronize_after_merge(merged, (), observed_remote_merged_sha=remote)


def test_rebases_to_observed_remote_sha_not_local_accepted_sha():
    merged = _job("PUB-001", "demo-repo", "MERGED")
    queued = _job("PUB-002", "demo-repo", "READY_TO_PUBLISH", github_base_sha="b" * 40)
    result = synchronize_after_merge(
        merged, (queued,), observed_remote_merged_sha=REMOTE_MERGE_SHA
    )
    assert result.new_released_sha == REMOTE_MERGE_SHA
    assert result.new_released_sha != merged.accepted_sha
    assert len(result.rebased_queued_jobs) == 1
    assert result.rebased_queued_jobs[0].github_base_sha == REMOTE_MERGE_SHA
    assert result.rebased_queued_jobs[0].publication_id == "PUB-002"
    assert queued.github_base_sha == "b" * 40


def test_excludes_other_repositories_and_nonqueued_jobs():
    merged = _job("PUB-001", "demo-repo", "MERGED")
    different_repo = _job("PUB-002", "other-repo", "READY_TO_PUBLISH")
    completed = _job("PUB-003", "demo-repo", "RELEASE_SYNCED")
    in_flight = _job("PUB-004", "demo-repo", "CI_PENDING")
    result = synchronize_after_merge(
        merged,
        (different_repo, completed, in_flight),
        observed_remote_merged_sha=REMOTE_MERGE_SHA,
    )
    assert result.rebased_queued_jobs == ()


@pytest.mark.parametrize(
    "state", ["READY_TO_PUBLISH", "PUBLICATION_DEFERRED", "MANUAL_POLICY_GATE"]
)
def test_actual_queued_states_remain_eligible_for_re_evaluation(state):
    merged = _job("PUB-001", "demo-repo", "MERGED")
    queued = _job("PUB-002", "demo-repo", state)
    result = synchronize_after_merge(
        merged, (queued,), observed_remote_merged_sha=REMOTE_MERGE_SHA
    )
    assert result.rebased_queued_jobs[0].current_state == state
    assert result.rebased_queued_jobs[0].github_base_sha == REMOTE_MERGE_SHA


def test_does_not_requeue_merged_job_itself():
    merged = _job("PUB-001", "demo-repo", "MERGED")
    result = synchronize_after_merge(
        merged, (merged,), observed_remote_merged_sha=REMOTE_MERGE_SHA
    )
    assert result.rebased_queued_jobs == ()


def test_empty_queue_returns_empty_result():
    merged = _job("PUB-001", "demo-repo", "MERGED")
    result = synchronize_after_merge(
        merged, (), observed_remote_merged_sha=REMOTE_MERGE_SHA
    )
    assert result.rebased_queued_jobs == ()
