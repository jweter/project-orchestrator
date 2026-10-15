"""Decision-only GitHub merge safety gate.

Never calls Git, GitHub, a merge executor, or release authority. Exact,
independently observed remote base identity is mandatory. A positive decision
is provisional: the authorized executor must recheck the remote head atomically.
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


def _exact_sha(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(ch in "0123456789abcdef" for ch in value)
    )


def decide_safe_to_merge(
    job: PublicationJob,
    *,
    current_default_branch_sha: str | None,
) -> MergeSafetyDecision:
    """Evaluate a MERGE_READY publication against observed GitHub branch HEAD.

    Missing/malformed identities are UNKNOWN, never SAFE or proven divergence.
    A demonstrated mismatch transitions the job to BASE_DIVERGED.
    This function cannot authorize or perform any remote mutation.
    """
    if job.current_state != "MERGE_READY":
        raise ValueError(
            f"decide_safe_to_merge requires MERGE_READY, got {job.current_state!r}"
        )

    if not _exact_sha(job.github_base_sha):
        return MergeSafetyDecision(
            safe_to_merge=False,
            resulting_job=job,
            reason="recorded GitHub base SHA missing or unverified; refusing merge",
        )
    if not _exact_sha(current_default_branch_sha):
        return MergeSafetyDecision(
            safe_to_merge=False,
            resulting_job=job,
            reason="observed remote default-branch SHA missing or unverified",
        )

    if job.github_base_sha != current_default_branch_sha:
        return MergeSafetyDecision(
            safe_to_merge=False,
            resulting_job=transition(job, "BASE_DIVERGED"),
            reason=(
                f"recorded base {job.github_base_sha!r} no longer matches "
                f"observed default-branch HEAD {current_default_branch_sha!r}"
            ),
        )

    return MergeSafetyDecision(
        safe_to_merge=True,
        resulting_job=job,
        reason=(
            "exact recorded and observed GitHub base SHAs match; provisional "
            "decision only; authorized executor must perform an atomic "
            "expected-remote-head check"
        ),
    )
