"""The iron-god/milestone/<milestone-id> ref convention.

`iron-god/milestone/<milestone-id>` protects an accepted milestone commit
permanently. Unlike the accepted-baseline ref (fast-forward-only advance)
and the candidate ref (freely replaceable via compare-and-swap), a
milestone ref is write-once: creating it again with the identical SHA is
a safe, idempotent no-op (so a crashed-and-replayed caller never errors
on retry), but creating it with a *different* SHA for the same milestone
id always fails. There is no clear or delete function here -- milestone
refs are permanent by design.

This module is not wired to any automatic or unattended call site.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

REF_PREFIX = "refs/iron-god/milestone"


class MilestoneRefError(RuntimeError):
    """Raised when a milestone ref read or write cannot proceed safely."""


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def milestone_ref_name(milestone_id: str) -> str:
    """The fully-qualified ref name for a milestone."""
    if not milestone_id or "/" in milestone_id or milestone_id.startswith("."):
        raise MilestoneRefError(f"invalid milestone identifier: {milestone_id!r}")
    return f"{REF_PREFIX}/{milestone_id}"


def get_milestone(repo_root: Path, milestone_id: str) -> str | None:
    """Return the milestone's recorded commit SHA, or None if unset."""
    ref = milestone_ref_name(milestone_id)
    result = _run_git(repo_root, "rev-parse", "--verify", "--quiet", ref)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


@dataclass(frozen=True)
class MilestoneRefWrite:
    milestone_id: str
    sha: str
    already_existed: bool


def create_milestone(repo_root: Path, milestone_id: str, sha: str) -> MilestoneRefWrite:
    """Permanently record a milestone's commit SHA.

    If the milestone id is already recorded with the identical SHA, this
    is a no-op success (idempotent replay). If it is already recorded
    with a *different* SHA, this always fails -- milestones never change.
    The first write uses `update-ref`'s must-not-exist compare-and-swap
    form (an empty old-value) so a concurrent create is caught atomically
    rather than silently overwritten.
    """
    ref = milestone_ref_name(milestone_id)
    verify = _run_git(repo_root, "rev-parse", "--verify", "--quiet", sha + "^{commit}")
    if verify.returncode != 0:
        raise MilestoneRefError(f"sha {sha!r} is not a valid commit")
    resolved_sha = verify.stdout.strip()

    current = get_milestone(repo_root, milestone_id)
    if current is not None:
        if current == resolved_sha:
            return MilestoneRefWrite(
                milestone_id=milestone_id,
                sha=resolved_sha,
                already_existed=True,
            )
        raise MilestoneRefError(
            f"milestone {milestone_id!r} is already recorded as {current!r}; "
            f"milestones are immutable and cannot become {resolved_sha!r}"
        )

    update = _run_git(repo_root, "update-ref", ref, resolved_sha, "")
    if update.returncode != 0:
        # A concurrent creator may have won the race; it is only safe to
        # treat this as success if it recorded the exact same SHA.
        final = get_milestone(repo_root, milestone_id)
        if final == resolved_sha:
            return MilestoneRefWrite(
                milestone_id=milestone_id,
                sha=resolved_sha,
                already_existed=True,
            )
        raise MilestoneRefError(
            f"update-ref failed for milestone {milestone_id!r}: "
            f"{update.stderr.strip()}; current value is {final!r}"
        )

    return MilestoneRefWrite(
        milestone_id=milestone_id, sha=resolved_sha, already_existed=False
    )
