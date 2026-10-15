"""Post-merge synchronization (roadmap section 11).

"After A merges: update released baseline; evaluate B against new
GitHub main." Given a job that has just reached MERGED, computes the
repository's new released-baseline SHA and returns every other queued
job for the same repository with its github_base_sha updated to that
new baseline, ready for re-evaluation. Performs no Git or GitHub
operation -- it only computes updated records for a caller to
separately persist and act on.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .iron_god_publication_job import PublicationJob


class PostMergeSyncError(RuntimeError):
    """Raised when asked to synchronize from a job that has not
    actually reached MERGED."""


@dataclass(frozen=True)
class PostMergeSyncResult:
    repository: str
    new_released_sha: str
    rebased_queued_jobs: tuple[PublicationJob, ...]


def synchronize_after_merge(
    merged_job: PublicationJob,
    queued_jobs: tuple[PublicationJob, ...],
) -> PostMergeSyncResult:
    """Given `merged_job` (must be MERGED), compute the repository's new
    released-baseline SHA (the merged job's accepted_sha) and return
    every other job in `queued_jobs` for the same repository with its
    github_base_sha rebased onto that new baseline. Jobs for a
    different repository are excluded; input jobs are never mutated.
    """
    if merged_job.current_state != "MERGED":
        raise PostMergeSyncError(
            f"synchronize_after_merge requires a MERGED job, got {merged_job.current_state!r}"
        )

    rebased = tuple(
        replace(job, github_base_sha=merged_job.accepted_sha)
        for job in queued_jobs
        if job.repository == merged_job.repository
    )

    return PostMergeSyncResult(
        repository=merged_job.repository,
        new_released_sha=merged_job.accepted_sha,
        rebased_queued_jobs=rebased,
    )
