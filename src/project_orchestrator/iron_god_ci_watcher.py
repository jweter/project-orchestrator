"""CI watcher.

Given an already-observed CI status string, decides the publication
state transition it implies, composing the publication state machine
(iron_god_publication_state_machine.py). Performs no network call or
GitHub API query itself -- obtaining the actual CI status is the
GitHub adapter's job (iron_god_github_adapter.py, not yet
implemented); this module only interprets an already-known status.
"""

from __future__ import annotations

from .iron_god_publication_job import PublicationJob
from .iron_god_publication_state_machine import transition

KNOWN_CI_STATUSES = frozenset({"pending", "success", "failure", "error", "cancelled"})

_FAILURE_STATUSES = frozenset({"failure", "error", "cancelled"})


class UnknownCIStatusError(RuntimeError):
    """Raised when an unrecognized CI status string is observed."""


def interpret_ci_status(job: PublicationJob, ci_status: str) -> PublicationJob:
    """Return a new PublicationJob reflecting what `ci_status` implies.

    "pending" leaves the job unchanged. "success" transitions to
    REVIEW_PENDING. "failure"/"error"/"cancelled" all transition to
    CI_FAILED. An unrecognized status raises rather than guessing. Any
    transition still goes through the state machine, so calling this
    when the job is not actually in CI_PENDING raises
    IllegalTransitionError rather than silently succeeding.
    """
    if ci_status not in KNOWN_CI_STATUSES:
        raise UnknownCIStatusError(f"unrecognized CI status: {ci_status!r}")

    if ci_status == "pending":
        return job
    if ci_status == "success":
        return transition(job, "REVIEW_PENDING")
    return transition(job, "CI_FAILED")
