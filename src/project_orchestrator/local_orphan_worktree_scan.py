"""Read-only inventory and classification of registered Git worktrees.

This module only ever reads repository state (`git worktree list`, `git
status`, `git ls-files`). It never prunes, removes, locks/unlocks, checks
out, resets, cleans, or otherwise mutates refs, the index, branches, or
worktree content.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .human_ownership import human_mode_active

_GIT_ENV_REDIRECT_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CEILING_DIRECTORIES",
)


class OwnerControlActive(RuntimeError):
    """Raised when owner control is active or unverified; the scan must stop."""


@dataclass(frozen=True)
class WorktreeEntry:
    path: Path
    head: str | None
    branch: str | None
    bare: bool
    detached: bool
    locked: bool
    lock_reason: str | None
    prunable: bool
    prunable_reason: str | None


@dataclass(frozen=True)
class WorktreeClassification:
    entry: WorktreeEntry
    missing: bool
    dirty: bool
    ambiguous: bool
    ignored_untracked_paths: tuple[str, ...]
    assume_unchanged_paths: tuple[str, ...]
    skip_worktree_paths: tuple[str, ...]
    notes: tuple[str, ...]


def _sanitized_git_env() -> dict[str, str]:
    """Strip repo/worktree-redirection variables so git only ever operates
    on the explicit `-C <path>` target, never an inherited GIT_DIR/_WORK_TREE."""
    env = dict(os.environ)
    for key in _GIT_ENV_REDIRECT_VARS:
        env.pop(key, None)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def _run_git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        env=_sanitized_git_env(),
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _parse_worktree_porcelain(output: str) -> list[WorktreeEntry]:
    entries: list[WorktreeEntry] = []
    state: dict[str, object] = {}

    def flush() -> None:
        if state.get("path") is not None:
            entries.append(
                WorktreeEntry(
                    path=state["path"],
                    head=state.get("head"),
                    branch=state.get("branch"),
                    bare=bool(state.get("bare", False)),
                    detached=bool(state.get("detached", False)),
                    locked=bool(state.get("locked", False)),
                    lock_reason=state.get("lock_reason"),
                    prunable=bool(state.get("prunable", False)),
                    prunable_reason=state.get("prunable_reason"),
                )
            )
        state.clear()

    for line in output.splitlines():
        if not line:
            flush()
            continue
        if line.startswith("worktree "):
            if state.get("path") is not None:
                flush()
            state["path"] = Path(line[len("worktree ") :])
        elif line.startswith("HEAD "):
            state["head"] = line[len("HEAD ") :]
        elif line.startswith("branch "):
            state["branch"] = line[len("branch ") :]
        elif line == "bare":
            state["bare"] = True
        elif line == "detached":
            state["detached"] = True
        elif line == "locked" or line.startswith("locked "):
            state["locked"] = True
            state["lock_reason"] = line[len("locked ") :] if line != "locked" else None
        elif line == "prunable" or line.startswith("prunable "):
            state["prunable"] = True
            state["prunable_reason"] = (
                line[len("prunable ") :] if line != "prunable" else None
            )
    flush()
    return entries


def _index_flagged_paths(repo_root: Path, flag: str) -> tuple[str, ...]:
    """`flag` is 'h' (assume-unchanged) or 'S' (skip-worktree), per `git ls-files -v`."""
    output = _run_git(repo_root, "ls-files", "-v")
    return tuple(line[2:] for line in output.splitlines() if line[:1] == flag)


def _ignored_or_untracked(repo_root: Path) -> tuple[str, ...]:
    output = _run_git(
        repo_root,
        "status",
        "--porcelain",
        "--ignored",
        "--untracked-files=all",
    )
    return tuple(line[3:] for line in output.splitlines() if line[:2] in ("??", "!!"))


def classify_worktree(entry: WorktreeEntry) -> WorktreeClassification:
    if not entry.path.is_dir():
        return WorktreeClassification(
            entry=entry,
            missing=True,
            dirty=False,
            ambiguous=False,
            ignored_untracked_paths=(),
            assume_unchanged_paths=(),
            skip_worktree_paths=(),
            notes=("worktree directory is missing from disk",),
        )

    notes: list[str] = []
    ambiguous = entry.detached and entry.branch is None and not entry.bare
    dirty = False
    ignored_untracked: tuple[str, ...] = ()
    assume_unchanged: tuple[str, ...] = ()
    skip_worktree: tuple[str, ...] = ()
    try:
        status = _run_git(entry.path, "status", "--porcelain")
        dirty = bool(status.strip())
        ignored_untracked = _ignored_or_untracked(entry.path)
        assume_unchanged = _index_flagged_paths(entry.path, "h")
        skip_worktree = _index_flagged_paths(entry.path, "S")
    except RuntimeError as exc:
        ambiguous = True
        notes.append(str(exc))

    if entry.locked:
        notes.append(f"locked: {entry.lock_reason or 'no reason recorded'}")
    if entry.prunable:
        notes.append(f"prunable: {entry.prunable_reason or 'no reason recorded'}")

    return WorktreeClassification(
        entry=entry,
        missing=False,
        dirty=dirty,
        ambiguous=ambiguous,
        ignored_untracked_paths=ignored_untracked,
        assume_unchanged_paths=assume_unchanged,
        skip_worktree_paths=skip_worktree,
        notes=tuple(notes),
    )


def scan_worktrees(repo_root: Path, state_root: Path) -> list[WorktreeClassification]:
    """Read-only worktree inventory; never mutates refs, index, or content.

    The registered worktree list is read twice -- once before classifying
    and once after -- so a registry change racing with this scan is reported
    as ambiguous instead of silently trusted. The owner-control gate is
    re-checked between the two reads for the same reason.
    """
    if human_mode_active(state_root):
        raise OwnerControlActive("owner control active or unverified; scan refused")

    first = _parse_worktree_porcelain(
        _run_git(repo_root, "worktree", "list", "--porcelain")
    )
    results = [classify_worktree(entry) for entry in first]

    if human_mode_active(state_root):
        raise OwnerControlActive("owner control active or unverified during scan")

    second = {
        entry.path: entry
        for entry in _parse_worktree_porcelain(
            _run_git(repo_root, "worktree", "list", "--porcelain")
        )
    }

    reconciled: list[WorktreeClassification] = []
    for classification in results:
        later = second.get(classification.entry.path)
        if later is None or later != classification.entry:
            reconciled.append(
                WorktreeClassification(
                    entry=classification.entry,
                    missing=classification.missing,
                    dirty=classification.dirty,
                    ambiguous=True,
                    ignored_untracked_paths=classification.ignored_untracked_paths,
                    assume_unchanged_paths=classification.assume_unchanged_paths,
                    skip_worktree_paths=classification.skip_worktree_paths,
                    notes=classification.notes
                    + ("worktree registry changed during scan",),
                )
            )
        else:
            reconciled.append(classification)
    return reconciled
