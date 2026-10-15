"""Review watcher.

Given an already-observed review status string, decides the
publication state transition it implies, composing the publication
state machine (iron_god_publication_state_machine.py). Performs no
network call or GitHub API query itself -- obtaining the actual
review status is the GitHub adapter's job (iron_god_github_adapter.py,
not yet implemented).
"""

from __future__ import annotations

from .iron_god_publication_job import PublicationJob
from .iron_god_publication_state_machine import transition

KNOWN_REVIEW_STATUSES = frozenset(
    {"pending", "approved", "changes_requested", "dismissed"}
)


class UnknownReviewStatusError(RuntimeError):
    """Raised when an unrecognized review status string is observed."""


def interpret_review_status(job: PublicationJob, review_status: str) -> PublicationJob:
    """Return a new PublicationJob reflecting what `review_status` implies.

    "pending" leaves the job unchanged. "approved" transitions to
    MERGE_READY. "changes_requested"/"dismissed" both transition to
    REVIEW_CHANGES_REQUIRED. An unrecognized status raises rather than
    guessing. Any transition still goes through the state machine, so
    calling this when the job is not actually in REVIEW_PENDING raises
    IllegalTransitionError rather than silently succeeding.
    """
    if review_status not in KNOWN_REVIEW_STATUSES:
        raise UnknownReviewStatusError(f"unrecognized review status: {review_status!r}")

    if review_status == "pending":
        return job
    if review_status == "approved":
        return transition(job, "MERGE_READY")
    return transition(job, "REVIEW_CHANGES_REQUIRED")
