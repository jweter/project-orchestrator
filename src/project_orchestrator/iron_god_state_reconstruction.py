"""State reconstruction engine (roadmap section 16).

Reconstructs a slice's status purely from Git ground truth --
candidate and milestone refs (iron_god_candidate_refs.py,
iron_god_milestone_refs.py) -- without relying on any orchestration
database. Losing the database must not mean losing developed
software: this module re-derives the same answer the database would
have held, directly from refs/iron-god/*.

Ambiguous evidence (a milestone at a different SHA than the
candidate) is reported as AMBIGUOUS and never silently resolved one
way or the other, per the roadmap: "Ambiguous work is quarantined. It
is never silently deleted." This module performs no ref or Git
mutation -- it is read-only reconstruction.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .iron_god_candidate_refs import get_candidate
from .iron_god_milestone_refs import get_milestone

NO_CANDIDATE = "NO_CANDIDATE"
CANDIDATE_UNACCEPTED = "CANDIDATE_UNACCEPTED"
ACCEPTED = "ACCEPTED"
AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class ReconstructedSliceState:
    slice_id: str
    status: str
    candidate_sha: str | None
    milestone_sha: str | None
    reason: str


def reconstruct_slice_state(
    repo_root: Path,
    slice_id: str,
    milestone_id: str,
) -> ReconstructedSliceState:
    """Reconstruct `slice_id`'s status purely from Git refs.

    NO_CANDIDATE: neither ref exists.
    CANDIDATE_UNACCEPTED: a candidate exists but no milestone yet.
    ACCEPTED: the milestone exists with no remaining candidate
    (cleared after acceptance), or the candidate and milestone agree
    on the same SHA.
    AMBIGUOUS: a milestone exists at a SHA different from the
    candidate's -- never silently resolved.
    """
    candidate_sha = get_candidate(repo_root, slice_id)
    milestone_sha = get_milestone(repo_root, milestone_id)

    if candidate_sha is None and milestone_sha is None:
        return ReconstructedSliceState(
            slice_id=slice_id,
            status=NO_CANDIDATE,
            candidate_sha=None,
            milestone_sha=None,
            reason="no candidate or milestone ref exists",
        )

    if candidate_sha is None:
        return ReconstructedSliceState(
            slice_id=slice_id,
            status=ACCEPTED,
            candidate_sha=None,
            milestone_sha=milestone_sha,
            reason="milestone exists with no remaining candidate ref",
        )

    if milestone_sha is None:
        return ReconstructedSliceState(
            slice_id=slice_id,
            status=CANDIDATE_UNACCEPTED,
            candidate_sha=candidate_sha,
            milestone_sha=None,
            reason="candidate exists but no milestone has been recorded for it yet",
        )

    if candidate_sha == milestone_sha:
        return ReconstructedSliceState(
            slice_id=slice_id,
            status=ACCEPTED,
            candidate_sha=candidate_sha,
            milestone_sha=milestone_sha,
            reason="candidate and milestone agree on the same SHA",
        )

    return ReconstructedSliceState(
        slice_id=slice_id,
        status=AMBIGUOUS,
        candidate_sha=candidate_sha,
        milestone_sha=milestone_sha,
        reason=(
            f"candidate SHA {candidate_sha!r} does not match milestone SHA "
            f"{milestone_sha!r}; ambiguous, requires quarantine/review, "
            "never silently resolved"
        ),
    )
