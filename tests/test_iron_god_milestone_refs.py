from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from project_orchestrator.iron_god_milestone_refs import (
    MilestoneRefError,
    create_milestone,
    get_milestone,
    milestone_ref_name,
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


def _commit(path: Path, name: str) -> str:
    (path / name).write_text("x\n", encoding="utf-8")
    _git(path, "add", name)
    _git(path, "commit", "-q", "-m", name)
    return _git(path, "rev-parse", "HEAD")


def test_milestone_ref_name_rejects_invalid_identifiers():
    with pytest.raises(MilestoneRefError):
        milestone_ref_name("")
    with pytest.raises(MilestoneRefError):
        milestone_ref_name("a/b")
    with pytest.raises(MilestoneRefError):
        milestone_ref_name(".hidden")


def test_get_milestone_returns_none_when_unset(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    assert get_milestone(repo, "M-001") is None


def test_create_milestone_sets_ref(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    head = _git(repo, "rev-parse", "HEAD")
    result = create_milestone(repo, "M-001", head)
    assert result.sha == head
    assert result.already_existed is False
    assert get_milestone(repo, "M-001") == head


def test_create_milestone_is_idempotent_with_same_sha(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    head = _git(repo, "rev-parse", "HEAD")
    create_milestone(repo, "M-001", head)

    result = create_milestone(repo, "M-001", head)
    assert result.already_existed is True
    assert result.sha == head
    assert get_milestone(repo, "M-001") == head


def test_create_milestone_rejects_different_sha_for_existing_milestone(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    first = _git(repo, "rev-parse", "HEAD")
    create_milestone(repo, "M-001", first)
    second = _commit(repo, "b.txt")

    with pytest.raises(MilestoneRefError):
        create_milestone(repo, "M-001", second)
    assert get_milestone(repo, "M-001") == first


def test_create_milestone_rejects_nonexistent_sha(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    with pytest.raises(MilestoneRefError):
        create_milestone(repo, "M-001", "f" * 40)
    assert get_milestone(repo, "M-001") is None


def test_different_milestones_are_independent(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    first = _git(repo, "rev-parse", "HEAD")
    second = _commit(repo, "b.txt")

    create_milestone(repo, "M-001", first)
    create_milestone(repo, "M-002", second)

    assert get_milestone(repo, "M-001") == first
    assert get_milestone(repo, "M-002") == second


def test_no_clear_or_delete_function_is_exposed():
    import project_orchestrator.iron_god_milestone_refs as module

    assert not hasattr(module, "clear_milestone")
    assert not hasattr(module, "delete_milestone")
