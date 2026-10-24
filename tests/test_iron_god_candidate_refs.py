from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from project_orchestrator.iron_god_candidate_refs import (
    CandidateRefError,
    candidate_ref_name,
    clear_candidate,
    get_candidate,
    set_candidate,
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


def test_candidate_ref_name_rejects_invalid_identifiers():
    with pytest.raises(CandidateRefError):
        candidate_ref_name("")
    with pytest.raises(CandidateRefError):
        candidate_ref_name("a/b")
    with pytest.raises(CandidateRefError):
        candidate_ref_name(".hidden")


def test_get_candidate_returns_none_when_unset(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    assert get_candidate(repo, "IG-099") is None


def test_set_candidate_from_unset_creates_ref(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    head = _git(repo, "rev-parse", "HEAD")
    result = set_candidate(repo, "IG-099", head, expected_current_sha=None)
    assert result.previous_sha is None
    assert result.new_sha == head
    assert get_candidate(repo, "IG-099") == head


def test_set_candidate_replaces_with_correct_expected_value(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    first = _git(repo, "rev-parse", "HEAD")
    set_candidate(repo, "IG-099", first, expected_current_sha=None)
    second = _commit(repo, "b.txt")

    result = set_candidate(repo, "IG-099", second, expected_current_sha=first)
    assert result.previous_sha == first
    assert result.new_sha == second
    assert get_candidate(repo, "IG-099") == second


def test_set_candidate_replacement_does_not_require_ancestry(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    first = _git(repo, "rev-parse", "HEAD")
    set_candidate(repo, "IG-099", first, expected_current_sha=None)

    # A repair attempt built fresh from `first`, unrelated to any later
    # candidate history -- still a legitimate replacement.
    _git(repo, "checkout", "-q", "-b", "repair", first)
    repair_sha = _commit(repo, "repair.txt")
    _git(repo, "checkout", "-q", "-")

    result = set_candidate(repo, "IG-099", repair_sha, expected_current_sha=first)
    assert result.new_sha == repair_sha
    assert get_candidate(repo, "IG-099") == repair_sha


def test_set_candidate_rejects_stale_expected_current(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    first = _git(repo, "rev-parse", "HEAD")
    set_candidate(repo, "IG-099", first, expected_current_sha=None)
    second = _commit(repo, "b.txt")
    set_candidate(repo, "IG-099", second, expected_current_sha=first)
    third = _commit(repo, "c.txt")

    with pytest.raises(CandidateRefError):
        set_candidate(repo, "IG-099", third, expected_current_sha=first)
    assert get_candidate(repo, "IG-099") == second


def test_set_candidate_rejects_nonexistent_sha(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    with pytest.raises(CandidateRefError):
        set_candidate(repo, "IG-099", "f" * 40, expected_current_sha=None)
    assert get_candidate(repo, "IG-099") is None


def test_clear_candidate_removes_ref_with_correct_expected_value(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    head = _git(repo, "rev-parse", "HEAD")
    set_candidate(repo, "IG-099", head, expected_current_sha=None)

    clear_candidate(repo, "IG-099", expected_current_sha=head)
    assert get_candidate(repo, "IG-099") is None


def test_clear_candidate_rejects_stale_expected_value(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    first = _git(repo, "rev-parse", "HEAD")
    set_candidate(repo, "IG-099", first, expected_current_sha=None)
    second = _commit(repo, "b.txt")
    set_candidate(repo, "IG-099", second, expected_current_sha=first)

    with pytest.raises(CandidateRefError):
        clear_candidate(repo, "IG-099", expected_current_sha=first)
    assert get_candidate(repo, "IG-099") == second


def test_different_slices_have_independent_candidate_refs(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    head = _git(repo, "rev-parse", "HEAD")
    set_candidate(repo, "IG-100", head, expected_current_sha=None)
    assert get_candidate(repo, "IG-100") == head
    assert get_candidate(repo, "IG-101") is None
