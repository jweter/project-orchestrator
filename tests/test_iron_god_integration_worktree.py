from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from project_orchestrator.iron_god_integration_worktree import (
    IntegrationWorktreeError,
    apply_candidate,
    ephemeral_integration_worktree,
)


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
    (path / "shared.txt").write_text("base\n", encoding="utf-8")
    _git(path, "add", "shared.txt")
    _git(path, "commit", "-q", "-m", "c1")
    return path


def test_creates_worktree_at_exact_base_sha_and_cleans_up(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    base_sha = _git(repo, "rev-parse", "HEAD")
    scratch = tmp_path / "scratch"
    seen_path = None

    with ephemeral_integration_worktree(
        repo, base_sha, scratch_root=scratch
    ) as worktree_path:
        seen_path = worktree_path
        assert worktree_path.exists()
        assert _git(worktree_path, "rev-parse", "HEAD") == base_sha

    assert not seen_path.exists()
    worktrees_after = _git(repo, "worktree", "list")
    assert str(seen_path) not in worktrees_after


def test_cleans_up_even_when_caller_block_raises(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    base_sha = _git(repo, "rev-parse", "HEAD")
    scratch = tmp_path / "scratch"
    seen_path = None

    with pytest.raises(RuntimeError, match="boom"):
        with ephemeral_integration_worktree(
            repo, base_sha, scratch_root=scratch
        ) as worktree_path:
            seen_path = worktree_path
            raise RuntimeError("boom")

    assert not seen_path.exists()


def test_invalid_base_sha_raises_and_creates_nothing(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    scratch = tmp_path / "scratch"
    before = _git(repo, "worktree", "list")

    with pytest.raises(IntegrationWorktreeError):
        with ephemeral_integration_worktree(repo, "f" * 40, scratch_root=scratch):
            pytest.fail("should never yield")

    after = _git(repo, "worktree", "list")
    assert before == after


def test_apply_candidate_clean_when_non_conflicting(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    base_sha = _git(repo, "rev-parse", "HEAD")

    (repo / "other.txt").write_text("new file\n", encoding="utf-8")
    _git(repo, "add", "other.txt")
    _git(repo, "commit", "-q", "-m", "candidate change")
    candidate_sha = _git(repo, "rev-parse", "HEAD")

    scratch = tmp_path / "scratch"
    with ephemeral_integration_worktree(
        repo, base_sha, scratch_root=scratch
    ) as worktree_path:
        result = apply_candidate(worktree_path, candidate_sha)
        assert result.clean is True
        assert (worktree_path / "other.txt").exists()


def test_apply_candidate_reports_conflict_and_leaves_no_unresolved_state(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    base_sha = _git(repo, "rev-parse", "HEAD")

    # Advance the "accepted baseline" past base_sha with its own edit.
    (repo / "shared.txt").write_text("advanced\n", encoding="utf-8")
    _git(repo, "add", "shared.txt")
    _git(repo, "commit", "-q", "-m", "advance")
    advanced_sha = _git(repo, "rev-parse", "HEAD")

    # Candidate diverges from base_sha with a conflicting edit to the same line.
    _git(repo, "checkout", "-q", "-b", "cand", base_sha)
    (repo / "shared.txt").write_text("candidate-change\n", encoding="utf-8")
    _git(repo, "add", "shared.txt")
    _git(repo, "commit", "-q", "-m", "candidate change")
    candidate_sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-")

    scratch = tmp_path / "scratch"
    with ephemeral_integration_worktree(
        repo, advanced_sha, scratch_root=scratch
    ) as worktree_path:
        result = apply_candidate(worktree_path, candidate_sha)
        assert result.clean is False
        assert result.conflict_output != ""
        status = _git(worktree_path, "status", "--porcelain=v1")
        assert "UU" not in status
        assert not (worktree_path / ".git" / "CHERRY_PICK_HEAD").exists()


def test_apply_candidate_rejects_invalid_sha(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    base_sha = _git(repo, "rev-parse", "HEAD")
    scratch = tmp_path / "scratch"

    with ephemeral_integration_worktree(
        repo, base_sha, scratch_root=scratch
    ) as worktree_path:
        with pytest.raises(IntegrationWorktreeError):
            apply_candidate(worktree_path, "f" * 40)
