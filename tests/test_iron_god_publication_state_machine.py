from __future__ import annotations

import pytest

from project_orchestrator.iron_god_publication_job import (
    PUBLICATION_STATES,
    validate_publication_job,
)
from project_orchestrator.iron_god_publication_state_machine import (
    ALLOWED_TRANSITIONS,
    IllegalTransitionError,
    legal_next_states,
    transition,
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


def test_every_publication_state_has_a_transition_entry():
    assert set(ALLOWED_TRANSITIONS) == set(PUBLICATION_STATES)


def test_legal_next_states_rejects_unknown_state():
    with pytest.raises(IllegalTransitionError):
        legal_next_states("NOT_A_REAL_STATE")


def test_release_synced_is_terminal():
    assert legal_next_states("RELEASE_SYNCED") == frozenset()


def test_transition_succeeds_and_does_not_mutate_input():
    job = _job("READY_TO_PUBLISH")
    updated = transition(job, "PUSHING")

    assert updated is not job
    assert updated.current_state == "PUSHING"
    assert job.current_state == "READY_TO_PUBLISH"


def test_transition_rejects_illegal_move():
    job = _job("READY_TO_PUBLISH")
    with pytest.raises(IllegalTransitionError):
        transition(job, "MERGED")


def test_transition_from_terminal_state_always_fails():
    job = _job("RELEASE_SYNCED")
    with pytest.raises(IllegalTransitionError):
        transition(job, "READY_TO_PUBLISH")


def test_full_happy_path_sequence():
    job = _job("READY_TO_PUBLISH")
    for next_state in (
        "PUSHING",
        "BRANCH_PUBLISHED",
        "PR_OPEN",
        "CI_PENDING",
        "REVIEW_PENDING",
        "MERGE_READY",
        "MERGED",
        "RELEASE_SYNCED",
    ):
        job = transition(job, next_state)
    assert job.current_state == "RELEASE_SYNCED"


def test_deferred_recovery_path():
    job = _job("CI_PENDING")
    job = transition(job, "CI_FAILED")
    job = transition(job, "PUBLICATION_DEFERRED")
    job = transition(job, "READY_TO_PUBLISH")
    assert job.current_state == "READY_TO_PUBLISH"
