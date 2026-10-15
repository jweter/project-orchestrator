"""One-active-PR-per-repo controller (roadmap section 11).

By default only one publication per repository may be actively
publishing at a time. Given a repository's known PublicationJobs,
decides which single job (if any) is allowed to proceed, and which
must remain queued. Pure decision logic over already-known job
records -- no GitHub or network operation is performed.
"""

from __future__ import annotations

from dataclasses import dataclass

from .iron_god_publication_job import TERMINAL_STATES, PublicationJob

IN_FLIGHT_STATES = frozenset(
    {
        "PUSHING",
        "BRANCH_PUBLISHED",
        "PR_OPEN",
        "CI_PENDING",
        "REVIEW_PENDING",
        "MERGE_READY",
    }
)


class MultipleActivePublicationsError(RuntimeError):
    """Raised when more than one job for the same repository is already
    in-flight -- this must never happen and signals a bug elsewhere,
    not something this function silently resolves."""


@dataclass(frozen=True)
class PublicationLaneDecision:
    repository: str
    active_publication_id: str | None
    queued_publication_ids: tuple[str, ...]


def decide_active_publication(
    repository: str,
    jobs: tuple[PublicationJob, ...],
) -> PublicationLaneDecision:
    """Decide which job (if any) may actively publish for `repository`.

    If a job for `repository` is already in-flight, it remains active
    and every other non-terminal job for that repository is queued. If
    none is in-flight, the earliest-listed READY_TO_PUBLISH job (by
    input order) becomes active and the rest stay queued. Jobs for
    other repositories are ignored; terminal jobs are excluded
    entirely from both results.
    """
    repo_jobs = [
        job
        for job in jobs
        if job.repository == repository and job.current_state not in TERMINAL_STATES
    ]

    in_flight = [job for job in repo_jobs if job.current_state in IN_FLIGHT_STATES]
    if len(in_flight) > 1:
        raise MultipleActivePublicationsError(
            f"{len(in_flight)} jobs are simultaneously in-flight for "
            f"{repository!r}: {[job.publication_id for job in in_flight]}"
        )

    if in_flight:
        active = in_flight[0]
    else:
        ready = [job for job in repo_jobs if job.current_state == "READY_TO_PUBLISH"]
        active = ready[0] if ready else None

    if active is None:
        queued = tuple(job.publication_id for job in repo_jobs)
        return PublicationLaneDecision(
            repository=repository,
            active_publication_id=None,
            queued_publication_ids=queued,
        )

    queued = tuple(
        job.publication_id
        for job in repo_jobs
        if job.publication_id != active.publication_id
    )
    return PublicationLaneDecision(
        repository=repository,
        active_publication_id=active.publication_id,
        queued_publication_ids=queued,
    )
