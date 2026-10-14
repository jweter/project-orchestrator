"""The iron-god/candidate/<slice-id> ref convention.

`iron-god/candidate/<slice-id>` marks a worker's committed output as a
candidate awaiting integration review. Unlike the accepted-baseline ref
(see iron_god_refs.py), a candidate ref is not required to be a
fast-forward descendant of its predecessor -- a repair attempt is
commonly built fresh from the original base SHA rather than from the
failed candidate. What it does guarantee is that a candidate is never
silently overwritten or removed: every write or delete requires the
caller to supply the ref's exact current value.

This module is not wired to any automatic or unattended call site.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

REF_PREFIX = "refs/iron-god/candidate"


class CandidateRefError(RuntimeError):
    """Raised when a candidate ref read or write cannot proceed safely."""


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def candidate_ref_name(slice_id: str) -> str:
    """The fully-qualified ref name for a slice's candidate."""
    if not slice_id or "/" in slice_id or slice_id.startswith("."):
        raise CandidateRefError(f"invalid slice identifier: {slice_id!r}")
    return f"{REF_PREFIX}/{slice_id}"


def get_candidate(repo_root: Path, slice_id: str) -> str | None:
    """Return the current candidate commit SHA for a slice, or None if unset."""
    ref = candidate_ref_name(slice_id)
    result = _run_git(repo_root, "rev-parse", "--verify", "--quiet", ref)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


@dataclass(frozen=True)
class CandidateRefWrite:
    slice_id: str
    previous_sha: str | None
    new_sha: str


def set_candidate(
    repo_root: Path,
    slice_id: str,
    new_sha: str,
    *,
    expected_current_sha: str | None,
) -> CandidateRefWrite:
    """Create or replace a slice's candidate ref.

    Fails closed unless `expected_current_sha` exactly matches the ref's
    actual current value, and `new_sha` resolves to a real commit. The
    write uses `update-ref`'s compare-and-swap form so a concurrent change
    is caught atomically rather than racily overwritten. No ancestry
    relationship between the previous and new candidate is required.
    """
    ref = candidate_ref_name(slice_id)
    current = get_candidate(repo_root, slice_id)
    if current != expected_current_sha:
        raise CandidateRefError(
            f"expected_current_sha {expected_current_sha!r} does not match "
            f"actual current candidate {current!r} for slice {slice_id!r}; "
            "refusing to write against a stale assumption"
        )

    verify = _run_git(
        repo_root, "rev-parse", "--verify", "--quiet", new_sha + "^{commit}"
    )
    if verify.returncode != 0:
        raise CandidateRefError(f"new_sha {new_sha!r} is not a valid commit")
    resolved_new_sha = verify.stdout.strip()

    if current is None:
        update = _run_git(repo_root, "update-ref", ref, resolved_new_sha)
    else:
        update = _run_git(repo_root, "update-ref", ref, resolved_new_sha, current)
    if update.returncode != 0:
        raise CandidateRefError(
            f"update-ref failed, likely a concurrent change: {update.stderr.strip()}"
        )

    return CandidateRefWrite(
        slice_id=slice_id, previous_sha=current, new_sha=resolved_new_sha
    )


def clear_candidate(
    repo_root: Path, slice_id: str, *, expected_current_sha: str
) -> None:
    """Remove a slice's candidate ref once it has been integrated or abandoned.

    Requires the exact current value, the same compare-and-swap discipline
    as a write. There is no bulk or force delete path -- only this single,
    exact-match removal of one slice's own candidate ref.
    """
    ref = candidate_ref_name(slice_id)
    current = get_candidate(repo_root, slice_id)
    if current != expected_current_sha:
        raise CandidateRefError(
            f"expected_current_sha {expected_current_sha!r} does not match "
            f"actual current candidate {current!r} for slice {slice_id!r}; "
            "refusing to clear against a stale assumption"
        )
    delete = _run_git(repo_root, "update-ref", "-d", ref, current)
    if delete.returncode != 0:
        raise CandidateRefError(
            f"update-ref -d failed, likely a concurrent change: {delete.stderr.strip()}"
        )
