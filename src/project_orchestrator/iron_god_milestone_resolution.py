"""Latest committed milestone resolution (RDI-001 wiring support).

local_continuous_worker.py's main() candidate-mode loop needs a
milestone_id to pass to accepted_event_dispatch_coordinator's
selector -- "what comes after milestone X was accepted" -- but on an
ordinary crash-safe poll (as opposed to a one-shot "I just committed
this" call) nothing before this module resolved which milestone X
actually is. This module composes an already-produced
LocalFirstState.snapshot() dict to resolve the most recently
COMMITTED milestone for a given repository.

Returns None (never raises) when no committed milestone exists yet
for that repository -- this is the legitimate "needs a human-created
bootstrap milestone" case (confirmed as the intended design for this
project), not an error. Raises only on a genuinely malformed or
internally inconsistent snapshot. This module never constructs its
own LocalFirstState or LocalGitRefs -- it operates only on a snapshot
the caller already produced, preserving the single-instance
invariant resident_dispatch_binding.py enforces elsewhere in this
same wiring effort. It performs no database write and no Git
operation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

COMMITTED = "COMMITTED"


class MilestoneResolutionError(RuntimeError):
    """Raised when the acceptance_intents snapshot is malformed or
    internally inconsistent -- never guessed past."""


@dataclass(frozen=True)
class LatestMilestone:
    milestone_id: str
    repository: str
    accepted_sha: str
    changed_at: str


def resolve_latest_committed_milestone(
    snapshot: dict[str, Any],
    repository: str,
) -> LatestMilestone | None:
    """Find the most recently COMMITTED acceptance intent for
    `repository` in `snapshot` (as returned by
    LocalFirstState.snapshot()).

    Returns None if no COMMITTED acceptance intent exists yet for
    `repository` -- the legitimate "nothing accepted yet, needs a
    human-created bootstrap milestone" case. Raises
    MilestoneResolutionError if `snapshot` is missing
    `acceptance_intents`, contains a malformed row, or contains a
    duplicate milestone_id -- an internal consistency violation the
    real schema's PRIMARY KEY should already prevent, but this
    function never trusts that silently.
    """
    intents = snapshot.get("acceptance_intents")
    if not isinstance(intents, list):
        raise MilestoneResolutionError("snapshot is missing acceptance_intents")

    seen_ids: set[str] = set()
    committed_for_repo: list[dict[str, Any]] = []
    for row in intents:
        if not isinstance(row, dict) or not isinstance(row.get("milestone_id"), str):
            raise MilestoneResolutionError(f"malformed acceptance_intents row: {row!r}")
        milestone_id = row["milestone_id"]
        if milestone_id in seen_ids:
            raise MilestoneResolutionError(
                f"duplicate milestone_id in snapshot: {milestone_id!r}"
            )
        seen_ids.add(milestone_id)
        if row.get("repository") == repository and row.get("state") == COMMITTED:
            committed_for_repo.append(row)

    if not committed_for_repo:
        return None

    latest = max(
        committed_for_repo, key=lambda row: (row["changed_at"], row["milestone_id"])
    )
    return LatestMilestone(
        milestone_id=latest["milestone_id"],
        repository=latest["repository"],
        accepted_sha=latest["accepted_sha"],
        changed_at=latest["changed_at"],
    )
