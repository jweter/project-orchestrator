from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from project_orchestrator.iron_god_combined_regression import (
    CombinedRegressionError,
    default_pytest_runner,
    run_combined_regression,
)
from project_orchestrator.iron_god_verification_receipt import GREEN, RED


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


def _commit(path: Path, name: str, content: str = "x\n") -> str:
    (path / name).write_text(content, encoding="utf-8")
    _git(path, "add", name)
    _git(path, "commit", "-q", "-m", name)
    return _git(path, "rev-parse", "HEAD")


def test_run_combined_regression_green_on_passing_runner(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    head = _git(repo, "rev-parse", "HEAD")

    def passing_runner(repo_root: Path) -> tuple[int, bool, bool]:
        return (0, False, False)

    receipt = run_combined_regression(
        repo, "demo-repo", head, regression_runner=passing_runner
    )
    assert receipt.status == GREEN
    assert receipt.head_sha == head


def test_run_combined_regression_red_on_failing_runner(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    head = _git(repo, "rev-parse", "HEAD")

    def failing_runner(repo_root: Path) -> tuple[int, bool, bool]:
        return (1, False, False)

    receipt = run_combined_regression(
        repo, "demo-repo", head, regression_runner=failing_runner
    )
    assert receipt.status == RED


def test_run_combined_regression_never_calls_runner_on_head_mismatch(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    calls = []

    def runner(repo_root: Path) -> tuple[int, bool, bool]:
        calls.append(1)
        return (0, False, False)

    with pytest.raises(CombinedRegressionError):
        run_combined_regression(repo, "demo-repo", "f" * 40, regression_runner=runner)
    assert calls == []


def test_run_combined_regression_raises_if_head_moves_during_run(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    head = _git(repo, "rev-parse", "HEAD")

    def sneaky_runner(repo_root: Path) -> tuple[int, bool, bool]:
        _commit(repo, "sneaky.txt")
        return (0, False, False)

    with pytest.raises(CombinedRegressionError):
        run_combined_regression(
            repo, "demo-repo", head, regression_runner=sneaky_runner
        )


def test_default_pytest_runner_reflects_real_pass(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    (repo / "test_sample.py").write_text(
        "def test_ok():\n    assert 1 + 1 == 2\n",
        encoding="utf-8",
    )
    _git(repo, "add", "test_sample.py")
    _git(repo, "commit", "-q", "-m", "add passing test")
    head = _git(repo, "rev-parse", "HEAD")

    receipt = run_combined_regression(
        repo,
        "demo-repo",
        head,
        regression_runner=default_pytest_runner(),
    )
    assert receipt.status == GREEN


def test_default_pytest_runner_reflects_real_failure(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    (repo / "test_sample.py").write_text(
        "def test_fails():\n    assert 1 + 1 == 3\n",
        encoding="utf-8",
    )
    _git(repo, "add", "test_sample.py")
    _git(repo, "commit", "-q", "-m", "add failing test")
    head = _git(repo, "rev-parse", "HEAD")

    receipt = run_combined_regression(
        repo,
        "demo-repo",
        head,
        regression_runner=default_pytest_runner(),
    )
    assert receipt.status == RED
