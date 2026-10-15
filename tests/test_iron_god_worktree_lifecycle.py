from __future__ import annotations

import inspect
import subprocess
from pathlib import Path

from project_orchestrator.iron_god_worktree_lifecycle import (
    WorktreeRemovalContext,
    assess_worktree_removal_safety,
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
    (path / "a.txt").write_text("1\n", encoding="utf-8")
    _git(path, "add", "a.txt")
    _git(path, "commit", "-q", "-m", "c1")
    return path


def _clean_context(path: Path, **overrides) -> WorktreeRemovalContext:
    defaults = dict(
        worktree_path=path,
        owning_worker_pid=None,
        candidate_resolution="ACCEPTED",
        required_protected_refs=(),
    )
    defaults.update(overrides)
    return WorktreeRemovalContext(**defaults)


def test_safe_when_every_criterion_passes(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    decision = assess_worktree_removal_safety(_clean_context(repo))
    assert decision.safe_to_remove is True


def test_unsafe_when_path_does_not_exist(tmp_path):
    missing = tmp_path / "does-not-exist"
    decision = assess_worktree_removal_safety(_clean_context(missing))
    assert decision.safe_to_remove is False
    assert any("does not exist" in reason for reason in decision.reasons)


def test_unsafe_when_active_worker_alive(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    decision = assess_worktree_removal_safety(
        _clean_context(
            repo, owning_worker_pid=1234, worker_process_is_alive=lambda pid: True
        )
    )
    assert decision.safe_to_remove is False
    assert any("owns this worktree" in reason for reason in decision.reasons)


def test_dead_owning_worker_does_not_by_itself_block_removal(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    decision = assess_worktree_removal_safety(
        _clean_context(
            repo, owning_worker_pid=1234, worker_process_is_alive=lambda pid: False
        )
    )
    assert decision.safe_to_remove is True


def test_worker_liveness_check_raising_fails_closed(tmp_path):
    repo = _init_repo(tmp_path / "repo")

    def raising_check(pid: int) -> bool:
        raise OSError("no such process")

    decision = assess_worktree_removal_safety(
        _clean_context(
            repo, owning_worker_pid=1234, worker_process_is_alive=raising_check
        )
    )
    assert decision.safe_to_remove is False


def test_unsafe_when_worktree_is_dirty(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    decision = assess_worktree_removal_safety(_clean_context(repo))
    assert decision.safe_to_remove is False
    assert any("uncommitted" in reason for reason in decision.reasons)


def test_unsafe_when_git_status_cannot_be_read(tmp_path):
    not_a_repo = tmp_path / "plain-dir"
    not_a_repo.mkdir()
    decision = assess_worktree_removal_safety(_clean_context(not_a_repo))
    assert decision.safe_to_remove is False
    assert any("failing closed" in reason for reason in decision.reasons)


def test_unsafe_when_candidate_unresolved(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    decision = assess_worktree_removal_safety(
        _clean_context(repo, candidate_resolution="PENDING")
    )
    assert decision.safe_to_remove is False


def test_unsafe_when_candidate_resolution_is_none(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    decision = assess_worktree_removal_safety(
        _clean_context(repo, candidate_resolution=None)
    )
    assert decision.safe_to_remove is False


def test_unsafe_when_required_ref_missing(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    decision = assess_worktree_removal_safety(
        _clean_context(
            repo,
            required_protected_refs=("refs/iron-god/milestone/M-001",),
            ref_exists=lambda ref: False,
        )
    )
    assert decision.safe_to_remove is False
    assert any("missing" in reason for reason in decision.reasons)


def test_safe_when_required_ref_present(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    decision = assess_worktree_removal_safety(
        _clean_context(
            repo,
            required_protected_refs=("refs/iron-god/milestone/M-001",),
            ref_exists=lambda ref: True,
        )
    )
    assert decision.safe_to_remove is True


def test_module_never_reads_age_or_mtime():
    import project_orchestrator.iron_god_worktree_lifecycle as module

    source = inspect.getsource(module)
    for forbidden in ("mtime", "st_mtime", "getmtime", "time.time", "datetime.now"):
        assert forbidden not in source, (
            f"module must never use {forbidden!r} as a safety signal"
        )


def test_module_exposes_no_removal_function():
    import project_orchestrator.iron_god_worktree_lifecycle as module

    for name in dir(module):
        assert "remove" not in name.lower() or name == "assess_worktree_removal_safety"
        assert "delete" not in name.lower()
        assert "prune" not in name.lower()
