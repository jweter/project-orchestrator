"""Transport-failure deferral (roadmap section 30).

"GitHub push fails -> Publication job becomes deferred. Development
continues." A push/transport failure always transitions the job
through TRANSPORT_BLOCKED to PUBLICATION_DEFERRED -- the only legal
path per the state machine (TRANSPORT_BLOCKED's sole next state is
PUBLICATION_DEFERRED). The retry policy (iron_god_retry_policy.py) is
composed separately to advise whether/when a future re-attempt from
PUBLICATION_DEFERRED is worthwhile; that advisory decision never
changes the resulting job state itself. Performs no network operation.
"""

from __future__ import annotations

from dataclasses import dataclass

from .iron_god_publication_job import PublicationJob
from .iron_god_publication_state_machine import transition
from .iron_god_retry_policy import MAX_ATTEMPTS, RetryDecision, decide_retry


@dataclass(frozen=True)
class TransportFailureOutcome:
    resulting_job: PublicationJob
    retry_decision: RetryDecision


def handle_transport_failure(
    job: PublicationJob,
    *,
    prior_attempts: int,
    max_attempts: int = MAX_ATTEMPTS,
) -> TransportFailureOutcome:
    """Handle a push/transport failure for `job`.

    The job always ends up PUBLICATION_DEFERRED (via TRANSPORT_BLOCKED),
    regardless of the retry decision -- this function never sleeps or
    retries itself. `retry_decision` is advisory only, for a future
    scheduler deciding whether/when to bring the job back from
    PUBLICATION_DEFERRED to READY_TO_PUBLISH.
    """
    decision = decide_retry(prior_attempts, max_attempts=max_attempts)
    resulting = transition(job, "TRANSPORT_BLOCKED")
    resulting = transition(resulting, "PUBLICATION_DEFERRED")
    return TransportFailureOutcome(resulting_job=resulting, retry_decision=decision)
