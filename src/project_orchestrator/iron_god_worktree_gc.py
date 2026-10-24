"""Worktree garbage collector (roadmap section 18: Safe Garbage Collection).

Actually removes a worktree, but only after independently re-confirming
safety via iron_god_worktree_lifecycle.assess_worktree_removal_safety
immediately before acting. This module never makes its own removal
decision -- it re-verifies a decision that may have been computed
earlier, since state can change between decision and action, and it
never uses file age/mtime as a safety signal.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .iron_god_worktree_lifecycle import (
    WorktreeRemovalContext,
    assess_worktree_removal_safety,
)


class WorktreeGCError(RuntimeError):
    """Raised when a worktree cannot be safely removed."""


@dataclass(frozen=True)
class WorktreeGCResult:
    worktree_path: Path
    removed: bool
    reasons: tuple[str, ...]


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def collect_worktree(
    repo_root: Path, context: WorktreeRemovalContext
) -> WorktreeGCResult:
    """Remove `context.worktree_path` only if assess_worktree_removal_safety
    says it is currently safe. The safety check is re-run here, immediately
    before acting, regardless of any earlier decision the caller may have
    already made -- this is the only sanctioned path to an actual removal.
    """
    decision = assess_worktree_removal_safety(context)
    if not decision.safe_to_remove:
        return WorktreeGCResult(
            worktree_path=context.worktree_path,
            removed=False,
            reasons=decision.reasons,
        )

    remove = _run_git(
        repo_root, "worktree", "remove", "--force", str(context.worktree_path)
    )
    if remove.returncode != 0:
        raise WorktreeGCError(
            f"git worktree remove failed for {context.worktree_path}: {remove.stderr.strip()}"
        )

    return WorktreeGCResult(
        worktree_path=context.worktree_path,
        removed=True,
        reasons=decision.reasons,
    )
