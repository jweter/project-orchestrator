from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from project_orchestrator.iron_god_refs import (
    AcceptedBaselineError,
    accepted_ref_name,
    advance_accepted_baseline,
    get_accepted_baseline,
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


def test_accepted_ref_name_rejects_invalid_identifiers():
    with pytest.raises(AcceptedBaselineError):
        accepted_ref_name("")
    with pytest.raises(AcceptedBaselineError):
        accepted_ref_name("owner/repo")
    with pytest.raises(AcceptedBaselineError):
        accepted_ref_name(".hidden")


def test_get_accepted_baseline_returns_none_when_unset(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    assert get_accepted_baseline(repo, "demo") is None


def test_advance_from_unset_creates_ref(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    head = _git(repo, "rev-parse", "HEAD")
    result = advance_accepted_baseline(repo, "demo", head, expected_parent_sha=None)
    assert result.previous_sha is None
    assert result.new_sha == head
    assert get_accepted_baseline(repo, "demo") == head


def test_advance_fast_forward_succeeds(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    first = _git(repo, "rev-parse", "HEAD")
    advance_accepted_baseline(repo, "demo", first, expected_parent_sha=None)
    second = _commit(repo, "b.txt")

    result = advance_accepted_baseline(repo, "demo", second, expected_parent_sha=first)
    assert result.previous_sha == first
    assert result.new_sha == second
    assert get_accepted_baseline(repo, "demo") == second


def test_advance_rejects_stale_expected_parent(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    first = _git(repo, "rev-parse", "HEAD")
    advance_accepted_baseline(repo, "demo", first, expected_parent_sha=None)
    second = _commit(repo, "b.txt")
    advance_accepted_baseline(repo, "demo", second, expected_parent_sha=first)
    third = _commit(repo, "c.txt")

    with pytest.raises(AcceptedBaselineError):
        advance_accepted_baseline(repo, "demo", third, expected_parent_sha=first)
    assert get_accepted_baseline(repo, "demo") == second


def test_advance_rejects_non_fast_forward_divergent_commit(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    first = _git(repo, "rev-parse", "HEAD")
    advance_accepted_baseline(repo, "demo", first, expected_parent_sha=None)
    second = _commit(repo, "b.txt")
    advance_accepted_baseline(repo, "demo", second, expected_parent_sha=first)

    # Branches from `first`, not from the now-current `second` -- so it is
    # not a descendant of the accepted baseline we are advancing from.
    _git(repo, "checkout", "-q", "-b", "side", first)
    divergent = _commit(repo, "side.txt")
    _git(repo, "checkout", "-q", "-")

    with pytest.raises(AcceptedBaselineError):
        advance_accepted_baseline(repo, "demo", divergent, expected_parent_sha=second)
    assert get_accepted_baseline(repo, "demo") == second


def test_advance_rejects_nonexistent_sha(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    with pytest.raises(AcceptedBaselineError):
        advance_accepted_baseline(repo, "demo", "f" * 40, expected_parent_sha=None)
    assert get_accepted_baseline(repo, "demo") is None


def test_advance_is_compare_and_swap_against_concurrent_move(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    first = _git(repo, "rev-parse", "HEAD")
    advance_accepted_baseline(repo, "demo", first, expected_parent_sha=None)
    second = _commit(repo, "b.txt")

    # Simulate a concurrent writer moving the ref out from under us between
    # our caller's read and this call's own internal re-check.
    _git(repo, "update-ref", accepted_ref_name("demo"), second, first)

    third = _commit(repo, "c.txt")
    with pytest.raises(AcceptedBaselineError):
        # Caller still believes the parent is `first`; the real current
        # value is now `second`, so this must be rejected.
        advance_accepted_baseline(repo, "demo", third, expected_parent_sha=first)
    assert get_accepted_baseline(repo, "demo") == second


def test_different_repositories_have_independent_refs(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    head = _git(repo, "rev-parse", "HEAD")
    advance_accepted_baseline(repo, "repo-a", head, expected_parent_sha=None)
    assert get_accepted_baseline(repo, "repo-a") == head
    assert get_accepted_baseline(repo, "repo-b") is None
