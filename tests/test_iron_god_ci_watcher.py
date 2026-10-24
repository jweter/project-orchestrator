from __future__ import annotations

import pytest

from project_orchestrator.iron_god_ci_watcher import (
    UnknownCIStatusError,
    interpret_ci_status,
)
from project_orchestrator.iron_god_publication_state_machine import (
    IllegalTransitionError,
)
from project_orchestrator.iron_god_publication_job import validate_publication_job


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
    job = _job("CI_PENDING")
    result = interpret_ci_status(job, "pending")
    assert result.current_state == "CI_PENDING"


def test_success_transitions_to_review_pending():
    job = _job("CI_PENDING")
    result = interpret_ci_status(job, "success")
    assert result.current_state == "REVIEW_PENDING"


@pytest.mark.parametrize("status", ["failure", "error", "cancelled"])
def test_failure_like_statuses_transition_to_ci_failed(status):
    job = _job("CI_PENDING")
    result = interpret_ci_status(job, status)
    assert result.current_state == "CI_FAILED"


def test_unknown_status_raises():
    job = _job("CI_PENDING")
    with pytest.raises(UnknownCIStatusError):
        interpret_ci_status(job, "weird_status")


def test_calling_when_not_in_ci_pending_propagates_illegal_transition():
    job = _job("READY_TO_PUBLISH")
    with pytest.raises(IllegalTransitionError):
        interpret_ci_status(job, "success")
