"""Safe auto-merge controller.

Given a job already in MERGE_READY (CI passed, review approved, per
the state machine) and an already-observed current default-branch
SHA, decides whether it is actually safe to merge now or whether the
recorded base has diverged since. Never performs the merge itself and
makes no network or GitHub API call -- the caller must supply the
current default-branch SHA.
"""

from __future__ import annotations

from dataclasses import dataclass

from .iron_god_publication_job import PublicationJob
from .iron_god_publication_state_machine import transition


@dataclass(frozen=True)
class MergeSafetyDecision:
    safe_to_merge: bool
    resulting_job: PublicationJob
    reason: str


def decide_safe_to_merge(
    job: PublicationJob,
    *,
    current_default_branch_sha: str,
) -> MergeSafetyDecision:
    """Decide whether `job` is safe to merge given the current
    default-branch SHA. Requires `job.current_state == "MERGE_READY"`.
    Never performs the merge itself -- if unsafe, transitions the job
    to BASE_DIVERGED so it can be re-evaluated rather than merged blindly.
    """
    if job.current_state != "MERGE_READY":
        raise ValueError(
            f"decide_safe_to_merge requires MERGE_READY, got {job.current_state!r}"
        )

    if job.github_base_sha is None:
        return MergeSafetyDecision(
            safe_to_merge=True,
            resulting_job=job,
            reason="no github_base_sha recorded; nothing to compare against, proceeding",
        )

    if job.github_base_sha != current_default_branch_sha:
        diverged = transition(job, "BASE_DIVERGED")
        return MergeSafetyDecision(
            safe_to_merge=False,
            resulting_job=diverged,
            reason=(
                f"recorded base {job.github_base_sha!r} no longer matches current "
                f"default branch {current_default_branch_sha!r}"
            ),
        )

    return MergeSafetyDecision(
        safe_to_merge=True,
        resulting_job=job,
        reason="base matches current default branch; safe to merge",
    )
