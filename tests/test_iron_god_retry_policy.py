from __future__ import annotations

import pytest

from project_orchestrator.iron_god_retry_policy import (
    BASE_DELAY_SECONDS,
    MAX_ATTEMPTS,
    MAX_DELAY_SECONDS,
    decide_retry,
    next_retry_delay,
)


def test_next_retry_delay_doubles_per_attempt():
    assert next_retry_delay(1) == BASE_DELAY_SECONDS
    assert next_retry_delay(2) == BASE_DELAY_SECONDS * 2
    assert next_retry_delay(3) == BASE_DELAY_SECONDS * 4


def test_next_retry_delay_caps_at_max():
    assert next_retry_delay(20) == MAX_DELAY_SECONDS


def test_next_retry_delay_rejects_attempt_below_one():
    with pytest.raises(ValueError):
        next_retry_delay(0)


def test_decide_retry_permits_within_budget():
    decision = decide_retry(0)
    assert decision.should_retry is True
    assert decision.attempt_number == 1
    assert decision.delay_seconds == BASE_DELAY_SECONDS


def test_decide_retry_permits_at_exact_boundary():
    decision = decide_retry(MAX_ATTEMPTS - 1)
    assert decision.should_retry is True
    assert decision.attempt_number == MAX_ATTEMPTS


def test_decide_retry_refuses_once_budget_exceeded():
    decision = decide_retry(MAX_ATTEMPTS)
    assert decision.should_retry is False
    assert decision.delay_seconds is None
    assert decision.attempt_number == MAX_ATTEMPTS + 1


def test_decide_retry_rejects_negative_prior_attempts():
    with pytest.raises(ValueError):
        decide_retry(-1)
