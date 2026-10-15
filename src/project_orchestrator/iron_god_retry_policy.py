"""Retry/backoff policy for publication-pipeline transport failures.

Given a prior-attempt count, decides the next retry delay (exponential
backoff with a cap) or that the retry budget is exhausted and the job
should be deferred for longer-term attention instead. Pure decision
logic -- no sleep, no actual retry, no network operation.
"""

from __future__ import annotations

from dataclasses import dataclass

BASE_DELAY_SECONDS = 30.0
MAX_DELAY_SECONDS = 3600.0
MAX_ATTEMPTS = 6


@dataclass(frozen=True)
class RetryDecision:
    should_retry: bool
    delay_seconds: float | None
    attempt_number: int
    reason: str


def next_retry_delay(attempt_number: int) -> float:
    """Exponential backoff capped at MAX_DELAY_SECONDS. `attempt_number`
    is 1-indexed: the first retry is attempt_number=1."""
    if attempt_number < 1:
        raise ValueError(f"attempt_number must be >= 1, got {attempt_number}")
    delay = BASE_DELAY_SECONDS * (2 ** (attempt_number - 1))
    return min(delay, MAX_DELAY_SECONDS)


def decide_retry(
    prior_attempts: int, *, max_attempts: int = MAX_ATTEMPTS
) -> RetryDecision:
    """Decide whether another retry attempt is permitted, and if so,
    with what delay. Once `max_attempts` is exceeded, refuses rather
    than retrying forever."""
    if prior_attempts < 0:
        raise ValueError(f"prior_attempts must be >= 0, got {prior_attempts}")

    attempt_number = prior_attempts + 1
    if attempt_number > max_attempts:
        return RetryDecision(
            should_retry=False,
            delay_seconds=None,
            attempt_number=attempt_number,
            reason=(
                f"exhausted retry budget ({max_attempts} attempts); "
                "defer for longer-term attention"
            ),
        )

    return RetryDecision(
        should_retry=True,
        delay_seconds=next_retry_delay(attempt_number),
        attempt_number=attempt_number,
        reason=f"attempt {attempt_number} of {max_attempts}; retrying with exponential backoff",
    )
