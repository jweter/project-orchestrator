from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from project_orchestrator.iron_god_exact_head_guard import (
    ExactHeadViolation,
    assert_exact_head,
    current_head,
    is_clean,
    run_with_exact_head_guard,
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


def test_current_head_returns_actual_sha(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    assert current_head(repo) == _git(repo, "rev-parse", "HEAD")


def test_current_head_raises_on_non_repo(tmp_path):
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    with pytest.raises(ExactHeadViolation):
        current_head(not_a_repo)


def test_is_clean_true_and_false(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    assert is_clean(repo) is True
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    assert is_clean(repo) is False


def test_assert_exact_head_passes_when_matching(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    head = current_head(repo)
    assert_exact_head(repo, head)  # does not raise


def test_assert_exact_head_raises_on_mismatch(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    with pytest.raises(ExactHeadViolation):
        assert_exact_head(repo, "f" * 40)


def test_assert_exact_head_require_clean_raises_when_dirty(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    head = current_head(repo)
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    with pytest.raises(ExactHeadViolation):
        assert_exact_head(repo, head, require_clean=True)


def test_run_with_exact_head_guard_returns_step_result_when_head_stable(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    head = current_head(repo)
    result = run_with_exact_head_guard(repo, head, lambda: "evidence")
    assert result == "evidence"


def test_run_with_exact_head_guard_does_not_call_step_if_head_already_wrong(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    calls = []

    def step():
        calls.append(1)
        return "should not happen"

    with pytest.raises(ExactHeadViolation):
        run_with_exact_head_guard(repo, "f" * 40, step)
    assert calls == []


def test_run_with_exact_head_guard_raises_if_head_moves_during_step(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    head = current_head(repo)

    def step():
        _commit(repo, "sneaky.txt")
        return "untrustworthy"

    with pytest.raises(ExactHeadViolation):
        run_with_exact_head_guard(repo, head, step)


def test_run_with_exact_head_guard_require_clean_before_blocks_dirty_start(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    head = current_head(repo)
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    calls = []

    def step():
        calls.append(1)
        return "x"

    with pytest.raises(ExactHeadViolation):
        run_with_exact_head_guard(repo, head, step, require_clean_before=True)
    assert calls == []
