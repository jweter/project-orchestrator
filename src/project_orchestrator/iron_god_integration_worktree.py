"""Ephemeral integration worktree controller.

Creates a throwaway Git worktree checked out at a given base SHA so a
candidate commit can be applied and verified in isolation, without ever
touching a real worker's worktree or any protected ref. This module owns
the full lifecycle of worktrees it creates -- categorically different
from removing a worker's own worktree (that question belongs to
iron_god_worktree_lifecycle.py's decision-only safety assessment, not
this module).

A candidate is applied by cherry-picking its exact commit onto the
ephemeral worktree's base. A conflicting cherry-pick is aborted and
reported, never worked around -- this module performs no automatic
conflict resolution.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


class IntegrationWorktreeError(RuntimeError):
    """Raised when an ephemeral integration worktree cannot be created,
    populated, or cleaned up safely."""


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


@dataclass(frozen=True)
class CandidateApplyResult:
    applied_sha: str
    clean: bool
    conflict_output: str


@contextlib.contextmanager
def ephemeral_integration_worktree(
    repo_root: Path,
    base_sha: str,
    *,
    scratch_root: Path,
) -> Iterator[Path]:
    """Create a throwaway worktree at `base_sha` under `scratch_root`,
    yield its path, and always remove it (and its throwaway branch) on
    exit, whether the caller's block succeeds or raises.
    """
    verify = _run_git(
        repo_root, "rev-parse", "--verify", "--quiet", base_sha + "^{commit}"
    )
    if verify.returncode != 0:
        raise IntegrationWorktreeError(f"base_sha {base_sha!r} is not a valid commit")
    resolved_base_sha = verify.stdout.strip()

    scratch_root.mkdir(parents=True, exist_ok=True)
    worktree_name = f"iron-god-integration-{uuid.uuid4().hex[:12]}"
    worktree_path = scratch_root / worktree_name
    branch_name = f"iron-god/integration-scratch/{worktree_name}"

    add = _run_git(
        repo_root,
        "worktree",
        "add",
        "-b",
        branch_name,
        str(worktree_path),
        resolved_base_sha,
    )
    if add.returncode != 0:
        raise IntegrationWorktreeError(
            f"could not create ephemeral worktree: {add.stderr.strip()}"
        )

    try:
        yield worktree_path
    finally:
        remove = _run_git(
            repo_root, "worktree", "remove", "--force", str(worktree_path)
        )
        if remove.returncode != 0 and worktree_path.exists():
            shutil.rmtree(worktree_path, ignore_errors=True)
            _run_git(repo_root, "worktree", "prune")
        _run_git(repo_root, "branch", "-D", branch_name)


def apply_candidate(worktree_path: Path, candidate_sha: str) -> CandidateApplyResult:
    """Cherry-pick `candidate_sha` onto the ephemeral worktree's current
    HEAD. A conflict aborts the cherry-pick and is reported as unclean,
    never resolved automatically."""
    verify = _run_git(
        worktree_path, "rev-parse", "--verify", "--quiet", candidate_sha + "^{commit}"
    )
    if verify.returncode != 0:
        raise IntegrationWorktreeError(
            f"candidate_sha {candidate_sha!r} is not a valid commit"
        )
    resolved_candidate_sha = verify.stdout.strip()
    original_head = _run_git(worktree_path, "rev-parse", "HEAD").stdout.strip()

    pick = _run_git(worktree_path, "cherry-pick", "--no-commit", resolved_candidate_sha)
    if pick.returncode != 0:
        _run_git(worktree_path, "cherry-pick", "--abort")
        # --abort does not always fully clear --no-commit conflict state;
        # force this ephemeral worktree back to its pre-pick HEAD so it is
        # always left clean regardless of that quirk.
        _run_git(worktree_path, "reset", "--hard", original_head)
        return CandidateApplyResult(
            applied_sha=resolved_candidate_sha,
            clean=False,
            conflict_output=(pick.stderr.strip() or pick.stdout.strip()),
        )
    return CandidateApplyResult(
        applied_sha=resolved_candidate_sha, clean=True, conflict_output=""
    )
