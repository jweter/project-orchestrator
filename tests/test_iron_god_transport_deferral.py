from __future__ import annotations

import pytest

from project_orchestrator.iron_god_publication_job import validate_publication_job
from project_orchestrator.iron_god_publication_state_machine import (
    IllegalTransitionError,
)
from project_orchestrator.iron_god_retry_policy import MAX_ATTEMPTS
from project_orchestrator.iron_god_transport_deferral import handle_transport_failure


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


@pytest.mark.parametrize("starting_state", ["PUSHING", "BRANCH_PUBLISHED"])
def test_transport_failure_resolves_to_deferred(starting_state):
    job = _job(starting_state)
    outcome = handle_transport_failure(job, prior_attempts=0)
    assert outcome.resulting_job.current_state == "PUBLICATION_DEFERRED"
    assert outcome.retry_decision.should_retry is True


def test_resulting_state_is_deferred_even_when_retry_budget_exhausted():
    job = _job("PUSHING")
    outcome = handle_transport_failure(job, prior_attempts=MAX_ATTEMPTS)
    assert outcome.resulting_job.current_state == "PUBLICATION_DEFERRED"
    assert outcome.retry_decision.should_retry is False


def test_illegal_starting_state_propagates():
    job = _job("READY_TO_PUBLISH")
    with pytest.raises(IllegalTransitionError):
        handle_transport_failure(job, prior_attempts=0)
