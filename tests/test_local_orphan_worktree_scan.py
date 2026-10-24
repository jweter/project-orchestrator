from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from project_orchestrator.local_orphan_worktree_scan import (
    OwnerControlActive,
    scan_worktrees,
)


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "README.md").write_text("root\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "init")
    return path


def _authorized_state_root(tmp_path: Path) -> Path:
    state_root = tmp_path / "flow-state"
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / "human-mode.json").write_text(
        json.dumps({"enabled": False}),
        encoding="utf-8",
    )
    return state_root


def _by_path(classifications, path: Path):
    for classification in classifications:
        if classification.entry.path == path:
            return classification
    raise AssertionError(f"no classification entry for {path}")


def test_detects_missing_worktree(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    wt = tmp_path / "wt-missing"
    _git(repo, "worktree", "add", "-b", "feature/missing", str(wt))
    import shutil

    shutil.rmtree(wt)

    results = scan_worktrees(repo, _authorized_state_root(tmp_path))
    entry = _by_path(results, wt)
    assert entry.missing is True
    assert entry.dirty is False


def test_detects_dirty_worktree(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    wt = tmp_path / "wt-dirty"
    _git(repo, "worktree", "add", "-b", "feature/dirty", str(wt))
    (wt / "README.md").write_text("changed\n", encoding="utf-8")

    results = scan_worktrees(repo, _authorized_state_root(tmp_path))
    entry = _by_path(results, wt)
    assert entry.missing is False
    assert entry.dirty is True


def test_detects_locked_worktree(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    wt = tmp_path / "wt-locked"
    _git(repo, "worktree", "add", "-b", "feature/locked", str(wt))
    _git(repo, "worktree", "lock", str(wt), "--reason", "manual-test-lock")

    results = scan_worktrees(repo, _authorized_state_root(tmp_path))
    entry = _by_path(results, wt)
    assert entry.entry.locked is True
    assert entry.entry.lock_reason == "manual-test-lock"
    assert any("locked" in note for note in entry.notes)


def test_detects_ignored_and_untracked_content(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    wt = tmp_path / "wt-content"
    _git(repo, "worktree", "add", "-b", "feature/content", str(wt))
    (wt / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (wt / "ignored.txt").write_text("secret\n", encoding="utf-8")
    (wt / "untracked.txt").write_text("new\n", encoding="utf-8")

    results = scan_worktrees(repo, _authorized_state_root(tmp_path))
    entry = _by_path(results, wt)
    assert "ignored.txt" in entry.ignored_untracked_paths
    assert "untracked.txt" in entry.ignored_untracked_paths
    assert ".gitignore" in entry.ignored_untracked_paths


def test_detects_assume_unchanged_and_skip_worktree_flags(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    wt = tmp_path / "wt-flags"
    _git(repo, "worktree", "add", "-b", "feature/flags", str(wt))
    (wt / "a.txt").write_text("a\n", encoding="utf-8")
    (wt / "b.txt").write_text("b\n", encoding="utf-8")
    _git(wt, "add", "a.txt", "b.txt")
    _git(wt, "commit", "-q", "-m", "add flagged files")
    _git(wt, "update-index", "--assume-unchanged", "a.txt")
    _git(wt, "update-index", "--skip-worktree", "b.txt")

    results = scan_worktrees(repo, _authorized_state_root(tmp_path))
    entry = _by_path(results, wt)
    assert "a.txt" in entry.assume_unchanged_paths
    assert "b.txt" in entry.skip_worktree_paths


def test_refuses_when_owner_control_active(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    state_root = tmp_path / "flow-state"
    state_root.mkdir()
    (state_root / "human-mode.json").write_text(
        json.dumps({"enabled": True}),
        encoding="utf-8",
    )
    with pytest.raises(OwnerControlActive):
        scan_worktrees(repo, state_root)


def test_scans_read_only_when_legacy_marker_missing(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    state_root = tmp_path / "flow-state-absent"
    original_head = _git(repo, "rev-parse", "HEAD")
    original_status = _git(repo, "status", "--porcelain")
    results = scan_worktrees(repo, state_root)
    entry = _by_path(results, repo)
    assert entry.missing is False
    assert isinstance(entry.dirty, bool)
    assert _git(repo, "rev-parse", "HEAD") == original_head
    assert _git(repo, "status", "--porcelain") == original_status


def test_main_worktree_is_present_and_not_missing(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    results = scan_worktrees(repo, _authorized_state_root(tmp_path))
    entry = _by_path(results, repo)
    assert entry.missing is False
