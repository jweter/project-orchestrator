"""The iron-god/recovery/<incident-id> ref convention.

`iron-god/recovery/<incident-id>` protects ambiguous or incident-related
work so it is never silently lost. Same write-once/permanent discipline
as the milestone ref (iron_god_milestone_refs.py): recording the
identical SHA again for the same incident id is a safe idempotent no-op,
but a *different* SHA for an already-recorded incident id always fails.
There is no clear or delete function -- a recovery ref can only be
superseded by a human resolving the incident through some other,
explicit channel, never by this module.

This module is not wired to any automatic or unattended call site.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

REF_PREFIX = "refs/iron-god/recovery"


class RecoveryRefError(RuntimeError):
    """Raised when a recovery ref read or write cannot proceed safely."""


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def recovery_ref_name(incident_id: str) -> str:
    """The fully-qualified ref name for an incident's recovery anchor."""
    if not incident_id or "/" in incident_id or incident_id.startswith("."):
        raise RecoveryRefError(f"invalid incident identifier: {incident_id!r}")
    return f"{REF_PREFIX}/{incident_id}"


def get_recovery_ref(repo_root: Path, incident_id: str) -> str | None:
    """Return the incident's recorded commit SHA, or None if unset."""
    ref = recovery_ref_name(incident_id)
    result = _run_git(repo_root, "rev-parse", "--verify", "--quiet", ref)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


@dataclass(frozen=True)
class RecoveryRefWrite:
    incident_id: str
    sha: str
    already_existed: bool


def record_recovery_ref(
    repo_root: Path, incident_id: str, sha: str
) -> RecoveryRefWrite:
    """Permanently anchor an incident's ambiguous/at-risk commit.

    If the incident id is already recorded with the identical SHA, this
    is a no-op success (idempotent replay). If it is already recorded
    with a *different* SHA, this always fails. The first write uses
    `update-ref`'s must-not-exist compare-and-swap form (an empty
    old-value) so a concurrent create is caught atomically rather than
    silently overwritten.
    """
    ref = recovery_ref_name(incident_id)
    verify = _run_git(repo_root, "rev-parse", "--verify", "--quiet", sha + "^{commit}")
    if verify.returncode != 0:
        raise RecoveryRefError(f"sha {sha!r} is not a valid commit")
    resolved_sha = verify.stdout.strip()

    current = get_recovery_ref(repo_root, incident_id)
    if current is not None:
        if current == resolved_sha:
            return RecoveryRefWrite(
                incident_id=incident_id,
                sha=resolved_sha,
                already_existed=True,
            )
        raise RecoveryRefError(
            f"incident {incident_id!r} is already anchored to {current!r}; "
            f"recovery refs are permanent and cannot become {resolved_sha!r}"
        )

    update = _run_git(repo_root, "update-ref", ref, resolved_sha, "")
    if update.returncode != 0:
        final = get_recovery_ref(repo_root, incident_id)
        if final == resolved_sha:
            return RecoveryRefWrite(
                incident_id=incident_id,
                sha=resolved_sha,
                already_existed=True,
            )
        raise RecoveryRefError(
            f"update-ref failed for incident {incident_id!r}: "
            f"{update.stderr.strip()}; current value is {final!r}"
        )

    return RecoveryRefWrite(
        incident_id=incident_id, sha=resolved_sha, already_existed=False
    )
