"""The iron-god/accepted/<repository> baseline ref convention.

`iron-god/accepted/<repository>` represents the newest locally verified and
integrated development state for a repository. Workers never write this
ref directly. This module provides the ref naming convention, a read-only
getter, and a guarded advance primitive that can only move the ref forward
as a fast-forward from an exact expected prior value, using Git's own
compare-and-swap `update-ref` form so the move is atomic at the Git level.

This module does not decide *when* a baseline should advance -- that policy
belongs to the Integration Controller (not yet built). It is also never
wired to any automatic or unattended call site; every call here is an
explicit, individually-reviewed action by a caller.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

REF_PREFIX = "refs/iron-god/accepted"


class AcceptedBaselineError(RuntimeError):
    """Raised when an accepted-baseline read or advance cannot proceed safely."""


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def accepted_ref_name(repository: str) -> str:
    """The fully-qualified ref name for a repository's accepted baseline."""
    if not repository or "/" in repository or repository.startswith("."):
        raise AcceptedBaselineError(f"invalid repository identifier: {repository!r}")
    return f"{REF_PREFIX}/{repository}"


def get_accepted_baseline(repo_root: Path, repository: str) -> str | None:
    """Return the current accepted-baseline commit SHA, or None if unset."""
    ref = accepted_ref_name(repository)
    result = _run_git(repo_root, "rev-parse", "--verify", "--quiet", ref)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


@dataclass(frozen=True)
class AcceptedBaselineAdvance:
    repository: str
    previous_sha: str | None
    new_sha: str


def advance_accepted_baseline(
    repo_root: Path,
    repository: str,
    new_sha: str,
    *,
    expected_parent_sha: str | None,
) -> AcceptedBaselineAdvance:
    """Move the accepted-baseline ref forward -- fast-forward only.

    Fails closed (raises AcceptedBaselineError) unless all of:
    - `expected_parent_sha` exactly matches the ref's current value (an
      explicit optimistic-concurrency precondition from the caller);
    - `new_sha` resolves to a real, existing commit;
    - `new_sha` is a descendant of the current ref value, or the ref is
      currently unset and `expected_parent_sha` is None.

    The actual ref write uses `git update-ref <ref> <new> <old>`, Git's own
    compare-and-swap form, so a concurrent change between this function's
    read and write is still caught atomically rather than racily overwritten.
    """
    ref = accepted_ref_name(repository)
    current = get_accepted_baseline(repo_root, repository)
    if current != expected_parent_sha:
        raise AcceptedBaselineError(
            f"expected_parent_sha {expected_parent_sha!r} does not match "
            f"current accepted baseline {current!r} for {repository!r}; "
            "refusing to advance against a stale assumption"
        )

    verify = _run_git(
        repo_root, "rev-parse", "--verify", "--quiet", new_sha + "^{commit}"
    )
    if verify.returncode != 0:
        raise AcceptedBaselineError(f"new_sha {new_sha!r} is not a valid commit")
    resolved_new_sha = verify.stdout.strip()

    if current is not None:
        ancestry = _run_git(
            repo_root, "merge-base", "--is-ancestor", current, resolved_new_sha
        )
        if ancestry.returncode != 0:
            raise AcceptedBaselineError(
                f"new_sha {resolved_new_sha!r} is not a fast-forward descendant "
                f"of current accepted baseline {current!r}; refusing to advance"
            )

    if current is None:
        update = _run_git(repo_root, "update-ref", ref, resolved_new_sha)
    else:
        update = _run_git(repo_root, "update-ref", ref, resolved_new_sha, current)
    if update.returncode != 0:
        raise AcceptedBaselineError(
            f"update-ref failed, likely a concurrent change: {update.stderr.strip()}"
        )

    return AcceptedBaselineAdvance(
        repository=repository,
        previous_sha=current,
        new_sha=resolved_new_sha,
    )
