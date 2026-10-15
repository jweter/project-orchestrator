"""Pure post-merge synchronization from an independently observed GitHub HEAD.

A local accepted commit and the remote merge result are separate identities.
This helper never calls Git/GitHub, publishes, or mutates any input job.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .iron_god_publication_job import PublicationJob


class PostMergeSyncError(RuntimeError):
    """The remote merge or its exact observed identity is not established."""


@dataclass(frozen=True)
class PostMergeSyncResult:
    repository: str
    new_released_sha: str
    rebased_queued_jobs: tuple[PublicationJob, ...]


def _exact_sha(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(ch in "0123456789abcdef" for ch in value)
    )


def synchronize_after_merge(
    merged_job: PublicationJob,
    queued_jobs: tuple[PublicationJob, ...],
    *,
    observed_remote_merged_sha: str,
) -> PostMergeSyncResult:
    """Compute deferred publication follow-up from the observed remote result.

    The caller must independently establish the remote merged SHA after the
    job reaches MERGED. Never infer remote HEAD from a local accepted_sha.
    Updating queued job expectations is not an authorization to publish them.
    """
    if merged_job.current_state != "MERGED":
        raise PostMergeSyncError(
            "synchronize_after_merge requires a MERGED job, got "
            f"{merged_job.current_state!r}"
        )
    if not _exact_sha(observed_remote_merged_sha):
        raise PostMergeSyncError("REMOTE_MERGED_SHA_UNVERIFIED")

    rebased = tuple(
        replace(job, github_base_sha=observed_remote_merged_sha)
        for job in queued_jobs
        if job.repository == merged_job.repository
        and job.publication_id != merged_job.publication_id
        and job.current_state
        in {"READY_TO_PUBLISH", "PUBLICATION_DEFERRED", "MANUAL_POLICY_GATE"}
    )
    return PostMergeSyncResult(
        repository=merged_job.repository,
        new_released_sha=observed_remote_merged_sha,
        rebased_queued_jobs=rebased,
    )
