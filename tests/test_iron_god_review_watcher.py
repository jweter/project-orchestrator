from __future__ import annotations

import pytest

from project_orchestrator.iron_god_publication_job import validate_publication_job
from project_orchestrator.iron_god_publication_state_machine import (
    IllegalTransitionError,
)
from project_orchestrator.iron_god_review_watcher import (
    UnknownReviewStatusError,
    interpret_review_status,
)


def _job(current_state: str):
    return validate_publication_job(
        dict(
            publication_id="PUB-001",
            repository="demo-repo",
            milestone_id="M-001",
            accepted_sha="a" * 40,
            parent_accepted_sha=None,
            github_base_sha=None,
            verification_receipt_ref="receipt-001",
            product_reality_receipt_ref=None,
            required_ci=("build",),
            required_review_policy="one-approval",
            publication_branch="iron-god/publication/PUB-001",
            current_state=current_state,
        )
    )


def test_pending_leaves_job_unchanged():
    job = _job("REVIEW_PENDING")
    result = interpret_review_status(job, "pending")
    assert result.current_state == "REVIEW_PENDING"


def test_approved_transitions_to_merge_ready():
    job = _job("REVIEW_PENDING")
    result = interpret_review_status(job, "approved")
    assert result.current_state == "MERGE_READY"


@pytest.mark.parametrize("status", ["changes_requested", "dismissed"])
def test_changes_needed_statuses_transition_to_review_changes_requested(status):
    job = _job("REVIEW_PENDING")
    result = interpret_review_status(job, status)
    assert result.current_state == "REVIEW_CHANGES_REQUIRED"


def test_unknown_status_raises():
    job = _job("REVIEW_PENDING")
    with pytest.raises(UnknownReviewStatusError):
        interpret_review_status(job, "weird_status")


def test_calling_when_not_in_review_pending_propagates_illegal_transition():
    job = _job("READY_TO_PUBLISH")
    with pytest.raises(IllegalTransitionError):
        interpret_review_status(job, "approved")
