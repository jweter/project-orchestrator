"""Worktree removal safety assessment -- decision-only, performs no removal.

Implements the "Safe Garbage Collection" criteria from the Iron God
roadmap (section 18): a worktree may only be removed if all of:

1. no active worker owns it
2. all meaningful changes are committed
3. its candidate is accepted, rejected, or otherwise resolved
4. required protected refs exist
5. (recovery policy permitting removal is the caller's own concern,
   expressed by which `required_protected_refs` it asks this function
   to check for)

"Never use age alone as evidence that work is disposable" -- this module
has no field or code path that reads file age or modification time, and
never will; age is simply not an input this function accepts.

This module exposes no function that deletes, prunes, or removes
anything. It only decides and explains. The actual removal action, if
ever built, is a separate, independently-authorized slice. It is also
not wired to any automatic or unattended call site.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

RESOLVED_CANDIDATE_STATES = frozenset({"ACCEPTED", "REJECTED", "ABANDONED"})


def _read_git_status(worktree_path: Path) -> str | None:
    """Return `git status --porcelain` output, or None if it could not be read."""
    result = subprocess.run(
        ["git", "-C", str(worktree_path), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout


@dataclass(frozen=True)
class WorktreeRemovalContext:
    """Facts the caller must supply, gathered as a fresh, consistent snapshot
    immediately before calling assess_worktree_removal_safety. This function
    never discovers these itself in a way that could go stale between
    gathering and acting."""

    worktree_path: Path
    owning_worker_pid: int | None
    candidate_resolution: str | None
    required_protected_refs: tuple[str, ...] = field(default_factory=tuple)
    worker_process_is_alive: Callable[[int], bool] | None = None
    ref_exists: Callable[[str], bool] | None = None


@dataclass(frozen=True)
class WorktreeRemovalDecision:
    worktree_path: Path
    safe_to_remove: bool
    reasons: tuple[str, ...]


def assess_worktree_removal_safety(
    context: WorktreeRemovalContext,
) -> WorktreeRemovalDecision:
    """Decide whether a worktree meets every Safe Garbage Collection
    criterion. Performs no removal, prune, or deletion of any kind."""
    reasons: list[str] = []
    safe = True

    if not context.worktree_path.exists():
        return WorktreeRemovalDecision(
            worktree_path=context.worktree_path,
            safe_to_remove=False,
            reasons=("worktree path does not exist",),
        )

    if context.owning_worker_pid is not None:
        alive = True
        if context.worker_process_is_alive is not None:
            try:
                alive = context.worker_process_is_alive(context.owning_worker_pid)
            except OSError:
                alive = True  # fail closed: assume alive if liveness cannot be checked
        if alive:
            safe = False
            reasons.append(
                f"an active worker (pid {context.owning_worker_pid}) owns this worktree"
            )
        else:
            reasons.append(
                f"owning worker pid {context.owning_worker_pid} is recorded but not alive"
            )
    else:
        reasons.append("no active worker owns this worktree")

    status = _read_git_status(context.worktree_path)
    if status is None:
        safe = False
        reasons.append("could not read worktree git status; failing closed")
    elif status.strip():
        safe = False
        reasons.append("worktree has uncommitted changes")
    else:
        reasons.append("worktree is clean (no uncommitted changes)")

    if context.candidate_resolution not in RESOLVED_CANDIDATE_STATES:
        safe = False
        reasons.append(
            f"candidate resolution is {context.candidate_resolution!r}, not one of "
            f"{sorted(RESOLVED_CANDIDATE_STATES)}"
        )
    else:
        reasons.append(f"candidate is resolved: {context.candidate_resolution}")

    missing_refs = [
        ref
        for ref in context.required_protected_refs
        if context.ref_exists is not None and not context.ref_exists(ref)
    ]
    if missing_refs:
        safe = False
        reasons.append(f"required protected refs are missing: {missing_refs}")
    elif context.required_protected_refs:
        reasons.append("all required protected refs exist")

    return WorktreeRemovalDecision(
        worktree_path=context.worktree_path,
        safe_to_remove=safe,
        reasons=tuple(reasons),
    )
