from __future__ import annotations

import subprocess
from pathlib import Path

from project_orchestrator.iron_god_candidate_refs import set_candidate
from project_orchestrator.iron_god_milestone_refs import create_milestone
from project_orchestrator.iron_god_state_reconstruction import (
    ACCEPTED,
    AMBIGUOUS,
    CANDIDATE_UNACCEPTED,
    NO_CANDIDATE,
    reconstruct_slice_state,
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


def test_no_refs_reconstructs_as_no_candidate(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    result = reconstruct_slice_state(repo, "IG-100", "M-100")
    assert result.status == NO_CANDIDATE
    assert result.candidate_sha is None
    assert result.milestone_sha is None


def test_candidate_without_milestone_is_unaccepted(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    head = _git(repo, "rev-parse", "HEAD")
    set_candidate(repo, "IG-100", head, expected_current_sha=None)

    result = reconstruct_slice_state(repo, "IG-100", "M-100")
    assert result.status == CANDIDATE_UNACCEPTED
    assert result.candidate_sha == head
    assert result.milestone_sha is None


def test_milestone_without_candidate_is_accepted(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    head = _git(repo, "rev-parse", "HEAD")
    create_milestone(repo, "M-100", head)

    result = reconstruct_slice_state(repo, "IG-100", "M-100")
    assert result.status == ACCEPTED
    assert result.candidate_sha is None
    assert result.milestone_sha == head


def test_matching_candidate_and_milestone_is_accepted(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    head = _git(repo, "rev-parse", "HEAD")
    set_candidate(repo, "IG-100", head, expected_current_sha=None)
    create_milestone(repo, "M-100", head)

    result = reconstruct_slice_state(repo, "IG-100", "M-100")
    assert result.status == ACCEPTED
    assert result.candidate_sha == head
    assert result.milestone_sha == head


def test_mismatched_candidate_and_milestone_is_ambiguous(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    first = _git(repo, "rev-parse", "HEAD")
    second = _commit(repo, "b.txt")
    set_candidate(repo, "IG-100", second, expected_current_sha=None)
    create_milestone(repo, "M-100", first)

    result = reconstruct_slice_state(repo, "IG-100", "M-100")
    assert result.status == AMBIGUOUS
    assert result.candidate_sha == second
    assert result.milestone_sha == first
