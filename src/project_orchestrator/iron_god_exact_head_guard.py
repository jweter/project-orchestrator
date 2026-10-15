"""Exact-head verification guard.

Formalizes a pattern already used informally in
local_continuous_worker.py's verify_and_commit() (re-checking HEAD after
running the full preflight, since preflight may modify the checkout and
a receipt must never be certified for the wrong commit). Any
verification tier should wrap its run through this guard rather than
trusting that HEAD did not move mid-run.

This module runs no build/test/preflight command itself -- it only
checks Git state before and after a caller-supplied step.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")


class ExactHeadViolation(RuntimeError):
    """Raised when a worktree's HEAD does not match the expected SHA, or
    cannot be read, or the worktree is unexpectedly dirty."""


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def current_head(worktree_path: Path) -> str:
    """Return the worktree's current HEAD commit SHA."""
    result = _run_git(worktree_path, "rev-parse", "HEAD")
    if result.returncode != 0:
        raise ExactHeadViolation(
            f"could not read HEAD of {worktree_path}: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def is_clean(worktree_path: Path) -> bool:
    """Return whether the worktree has no uncommitted changes."""
    result = _run_git(worktree_path, "status", "--porcelain")
    if result.returncode != 0:
        raise ExactHeadViolation(
            f"could not read git status of {worktree_path}: {result.stderr.strip()}"
        )
    return result.stdout.strip() == ""


def assert_exact_head(
    worktree_path: Path,
    expected_sha: str,
    *,
    require_clean: bool = False,
) -> None:
    """Raise ExactHeadViolation unless HEAD exactly equals expected_sha,
    and, if require_clean, the worktree has no uncommitted changes."""
    actual = current_head(worktree_path)
    if actual != expected_sha:
        raise ExactHeadViolation(
            f"expected HEAD {expected_sha!r} but found {actual!r} in {worktree_path}"
        )
    if require_clean and not is_clean(worktree_path):
        raise ExactHeadViolation(
            f"worktree {worktree_path} is not clean at exact head {expected_sha!r}"
        )


def run_with_exact_head_guard(
    worktree_path: Path,
    expected_sha: str,
    step: Callable[[], T],
    *,
    require_clean_before: bool = True,
) -> T:
    """Run `step()` only while HEAD provably equals expected_sha both
    before and after.

    If HEAD does not match before running, `step` is never called at
    all. If HEAD moved during `step`, this raises ExactHeadViolation
    instead of returning -- the caller must never treat `step`'s return
    value as evidence for `expected_sha` once this raises.
    """
    assert_exact_head(worktree_path, expected_sha, require_clean=require_clean_before)
    result = step()
    assert_exact_head(worktree_path, expected_sha, require_clean=False)
    return result
