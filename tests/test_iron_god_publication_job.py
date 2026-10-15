from __future__ import annotations

import dataclasses

import pytest

from project_orchestrator.iron_god_publication_job import (
    PublicationJobError,
    RetryAttempt,
    REQUIRED_FIELDS,
    validate_publication_job,
)


def _payload(**overrides) -> dict:
    defaults = dict(
        publication_id="PUB-001",
        repository="demo-repo",
        milestone_id="M-001",
        accepted_sha="a" * 40,
        parent_accepted_sha=None,
        github_base_sha=None,
        verification_receipt_ref="receipt-001",
        product_reality_receipt_ref=None,
        required_ci=("build", "test"),
        required_review_policy="one-approval",
        publication_branch="iron-god/publication/PUB-001",
        current_state="READY_TO_PUBLISH",
    )
    defaults.update(overrides)
    return defaults


def test_validate_publication_job_succeeds_on_full_payload():
    job = validate_publication_job(_payload())
    assert job.publication_id == "PUB-001"
    assert job.required_ci == ("build", "test")
    assert job.retry_history == ()
    assert job.is_terminal() is False


@pytest.mark.parametrize("missing", REQUIRED_FIELDS)
def test_validate_publication_job_rejects_missing_field(missing):
    payload = _payload()
    del payload[missing]
    with pytest.raises(PublicationJobError):
        validate_publication_job(payload)


def test_validate_publication_job_rejects_unknown_state():
    with pytest.raises(PublicationJobError):
        validate_publication_job(_payload(current_state="NOT_A_REAL_STATE"))


def test_is_terminal_true_only_for_release_synced():
    job = validate_publication_job(_payload(current_state="RELEASE_SYNCED"))
    assert job.is_terminal() is True
    job = validate_publication_job(_payload(current_state="MERGED"))
    assert job.is_terminal() is False


def test_with_retry_returns_new_immutable_instance():
    job = validate_publication_job(_payload())
    attempt = RetryAttempt(
        attempt_number=1, occurred_at=1000.0, error="transport blocked"
    )

    updated = job.with_retry(attempt)

    assert updated is not job
    assert updated.retry_history == (attempt,)
    assert updated.last_error == "transport blocked"
    # Original instance is untouched.
    assert job.retry_history == ()
    assert job.last_error is None


def test_publication_job_is_frozen():
    job = validate_publication_job(_payload())
    with pytest.raises(dataclasses.FrozenInstanceError):
        job.current_state = "MERGED"
