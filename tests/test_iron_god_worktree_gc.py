from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from project_orchestrator.iron_god_worktree_gc import WorktreeGCError, collect_worktree
from project_orchestrator.iron_god_worktree_lifecycle import WorktreeRemovalContext


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "a.txt").write_text("1\n", encoding="utf-8")
    _git(path, "add", "a.txt")
    _git(path, "commit", "-q", "-m", "c1")
    return path


def _context(worktree_path: Path, **overrides) -> WorktreeRemovalContext:
    defaults = dict(
        worktree_path=worktree_path,
        owning_worker_pid=None,
        candidate_resolution="ACCEPTED",
        required_protected_refs=(),
    )
    defaults.update(overrides)
    return WorktreeRemovalContext(**defaults)


def test_removes_worktree_when_safe(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    wt = tmp_path / "wt-safe"
    _git(repo, "worktree", "add", "-b", "feature/safe", str(wt))

    result = collect_worktree(repo, _context(wt))

    assert result.removed is True
    assert not wt.exists()
    assert str(wt) not in _git(repo, "worktree", "list")


def test_leaves_worktree_untouched_when_unsafe(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    wt = tmp_path / "wt-dirty"
    _git(repo, "worktree", "add", "-b", "feature/dirty", str(wt))
    (wt / "a.txt").write_text("changed\n", encoding="utf-8")

    result = collect_worktree(repo, _context(wt))

    assert result.removed is False
    assert wt.exists()
    assert wt.as_posix() in _git(repo, "worktree", "list")


def test_raises_when_underlying_remove_fails(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    # A clean, resolved, unowned git repo that was never registered as a
    # worktree of `repo` -- passes the safety assessment but `git worktree
    # remove` against `repo` must fail for it.
    not_a_worktree = _init_repo(tmp_path / "not-a-worktree")

    with pytest.raises(WorktreeGCError):
        collect_worktree(repo, _context(not_a_worktree))
    assert not_a_worktree.exists()
