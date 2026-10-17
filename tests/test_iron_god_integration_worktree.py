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
    with pytest.raises(
        IntegrationWorktreeError, match="preserved integration worktree"
    ):
        with ephemeral_integration_worktree(
            repo, base_sha, scratch_root=scratch
        ) as worktree_path:
            result = apply_candidate(worktree_path, candidate_sha)
            assert result.clean is True
            assert (worktree_path / "other.txt").exists()

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


def test_dirty_integration_worktree_and_branch_are_preserved(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    base_sha = _git(repo, "rev-parse", "HEAD")
    scratch = tmp_path / "scratch"
    worktree_path = None

    with pytest.raises(
        IntegrationWorktreeError, match="preserved integration worktree"
    ):
        with ephemeral_integration_worktree(
            repo, base_sha, scratch_root=scratch
        ) as worktree_path:
            (worktree_path / "unresolved.txt").write_text("keep me\n", encoding="utf-8")

    assert worktree_path is not None
    assert (worktree_path / "unresolved.txt").read_text(encoding="utf-8") == "keep me\n"
    assert worktree_path.as_posix() in _git(repo, "worktree", "list")
    assert _git(worktree_path, "branch", "--show-current").startswith(
        "iron-god/integration-scratch/"
    )


def test_new_integration_commit_is_preserved(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    base_sha = _git(repo, "rev-parse", "HEAD")
    worktree_path = None

    with pytest.raises(
        IntegrationWorktreeError, match="preserved integration worktree"
    ):
        with ephemeral_integration_worktree(
            repo, base_sha, scratch_root=tmp_path / "scratch"
        ) as worktree_path:
            (worktree_path / "new.txt").write_text("valuable\n", encoding="utf-8")
            _git(worktree_path, "add", "new.txt")
            _git(worktree_path, "commit", "-q", "-m", "valuable change")

    assert worktree_path is not None
    assert worktree_path.exists()
    assert _git(worktree_path, "rev-parse", "HEAD") != base_sha


def test_failed_git_removal_preserves_worktree_and_branch(tmp_path, monkeypatch):
    import project_orchestrator.iron_god_integration_worktree as module

    repo = _init_repo(tmp_path / "repo")
    base_sha = _git(repo, "rev-parse", "HEAD")
    real_run_git = module._run_git
    worktree_path = None

    def refuse_removal(repo_root, *args):
        if args[:2] == ("worktree", "remove"):
            return subprocess.CompletedProcess(args, 1, "", "simulated refusal")
        return real_run_git(repo_root, *args)

    monkeypatch.setattr(module, "_run_git", refuse_removal)
    with pytest.raises(IntegrationWorktreeError, match="simulated refusal"):
        with ephemeral_integration_worktree(
            repo, base_sha, scratch_root=tmp_path / "scratch"
        ) as worktree_path:
            pass

    assert worktree_path is not None
    assert worktree_path.exists()
    assert worktree_path.as_posix() in _git(repo, "worktree", "list")
    assert _git(worktree_path, "branch", "--show-current").startswith(
        "iron-god/integration-scratch/"
    )
