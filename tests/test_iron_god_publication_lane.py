from __future__ import annotations

import pytest

from project_orchestrator.iron_god_publication_job import validate_publication_job
from project_orchestrator.iron_god_publication_lane import (
    MultipleActivePublicationsError,
    decide_active_publication,
)


def _job(publication_id: str, repository: str, current_state: str):
    return validate_publication_job(
        dict(
            publication_id=publication_id,
            repository=repository,
            milestone_id=f"M-{publication_id}",
            accepted_sha="a" * 40,
            parent_accepted_sha=None,
            github_base_sha=None,
            verification_receipt_ref="receipt-001",
            product_reality_receipt_ref=None,
            required_ci=("build",),
            required_review_policy="one-approval",
            publication_branch=f"iron-god/publication/{publication_id}",
            current_state=current_state,
        )
    )


def test_no_jobs_yields_no_active_publication():
    decision = decide_active_publication("demo-repo", ())
    assert decision.active_publication_id is None
    assert decision.queued_publication_ids == ()


def test_single_ready_job_becomes_active():
    job = _job("PUB-001", "demo-repo", "READY_TO_PUBLISH")
    decision = decide_active_publication("demo-repo", (job,))
    assert decision.active_publication_id == "PUB-001"
    assert decision.queued_publication_ids == ()


def test_earliest_ready_job_becomes_active_rest_queued():
    jobs = (
        _job("PUB-001", "demo-repo", "READY_TO_PUBLISH"),
        _job("PUB-002", "demo-repo", "READY_TO_PUBLISH"),
    )
    decision = decide_active_publication("demo-repo", jobs)
    assert decision.active_publication_id == "PUB-001"
    assert decision.queued_publication_ids == ("PUB-002",)


def test_in_flight_job_stays_active_over_ready_jobs():
    jobs = (
        _job("PUB-001", "demo-repo", "READY_TO_PUBLISH"),
        _job("PUB-002", "demo-repo", "PR_OPEN"),
    )
    decision = decide_active_publication("demo-repo", jobs)
    assert decision.active_publication_id == "PUB-002"
    assert decision.queued_publication_ids == ("PUB-001",)


def test_two_in_flight_jobs_raises():
    jobs = (
        _job("PUB-001", "demo-repo", "PR_OPEN"),
        _job("PUB-002", "demo-repo", "CI_PENDING"),
    )
    with pytest.raises(MultipleActivePublicationsError):
        decide_active_publication("demo-repo", jobs)


def test_jobs_for_other_repository_are_ignored():
    jobs = (
        _job("PUB-001", "other-repo", "PR_OPEN"),
        _job("PUB-002", "demo-repo", "READY_TO_PUBLISH"),
    )
    decision = decide_active_publication("demo-repo", jobs)
    assert decision.active_publication_id == "PUB-002"
    assert decision.queued_publication_ids == ()


def test_terminal_jobs_excluded_from_both_results():
    jobs = (
        _job("PUB-001", "demo-repo", "RELEASE_SYNCED"),
        _job("PUB-002", "demo-repo", "READY_TO_PUBLISH"),
    )
    decision = decide_active_publication("demo-repo", jobs)
    assert decision.active_publication_id == "PUB-002"
    assert decision.queued_publication_ids == ()


def test_no_ready_or_in_flight_jobs_leaves_them_all_queued():
    jobs = (
        _job("PUB-001", "demo-repo", "CI_FAILED"),
        _job("PUB-002", "demo-repo", "PUBLICATION_DEFERRED"),
    )
    decision = decide_active_publication("demo-repo", jobs)
    assert decision.active_publication_id is None
    assert set(decision.queued_publication_ids) == {"PUB-001", "PUB-002"}
