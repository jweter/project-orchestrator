from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from project_orchestrator.iron_god_acceptance import AcceptanceError, accept_candidate
from project_orchestrator.iron_god_milestone_refs import create_milestone, get_milestone
from project_orchestrator.iron_god_refs import (
    advance_accepted_baseline,
    get_accepted_baseline,
)
from project_orchestrator.iron_god_verification_receipt import GateResult, build_receipt


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


def _green_receipt(head_sha: str, repository: str):
    gates = (GateResult("lint", 0, False, False), GateResult("tests", 0, False, False))
    return build_receipt(head_sha, repository, gates)


def _red_receipt(head_sha: str, repository: str):
    gates = (GateResult("lint", 0, False, False), GateResult("tests", 1, False, False))
    return build_receipt(head_sha, repository, gates)


def test_accept_candidate_advances_baseline_and_records_milestone(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    first = _git(repo, "rev-parse", "HEAD")
    second = _commit(repo, "b.txt")
    receipt = _green_receipt(second, "demo-repo")

    result = accept_candidate(
        repo,
        "demo-repo",
        receipt,
        expected_parent_sha=None,
        milestone_id="M-001",
    )
    assert result.accepted_sha == second
    assert result.previous_baseline_sha is None
    assert get_accepted_baseline(repo, "demo-repo") == second
    assert get_milestone(repo, "M-001") == second


def test_accept_candidate_refuses_non_green_receipt(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    head = _git(repo, "rev-parse", "HEAD")
    receipt = _red_receipt(head, "demo-repo")

    with pytest.raises(AcceptanceError):
        accept_candidate(
            repo,
            "demo-repo",
            receipt,
            expected_parent_sha=None,
            milestone_id="M-001",
        )
    assert get_accepted_baseline(repo, "demo-repo") is None


def test_accept_candidate_refuses_repository_mismatch(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    head = _git(repo, "rev-parse", "HEAD")
    receipt = _green_receipt(head, "other-repo")

    with pytest.raises(AcceptanceError):
        accept_candidate(
            repo,
            "demo-repo",
            receipt,
            expected_parent_sha=None,
            milestone_id="M-001",
        )
    assert get_accepted_baseline(repo, "demo-repo") is None


def test_accept_candidate_refuses_stale_expected_parent(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    first = _git(repo, "rev-parse", "HEAD")
    advance_accepted_baseline(repo, "demo-repo", first, expected_parent_sha=None)
    second = _commit(repo, "b.txt")
    third = _commit(repo, "c.txt")
    receipt = _green_receipt(third, "demo-repo")

    with pytest.raises(AcceptanceError):
        accept_candidate(
            repo,
            "demo-repo",
            receipt,
            expected_parent_sha=second,
            milestone_id="M-001",
        )
    assert get_accepted_baseline(repo, "demo-repo") == first


def test_accept_candidate_refuses_non_fast_forward(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    first = _git(repo, "rev-parse", "HEAD")
    advance_accepted_baseline(repo, "demo-repo", first, expected_parent_sha=None)
    second = _commit(repo, "b.txt")
    advance_accepted_baseline(repo, "demo-repo", second, expected_parent_sha=first)

    # Branches from `first`, not from the now-current `second` -- so it is
    # not a descendant of the accepted baseline we are advancing from.
    _git(repo, "checkout", "-q", "-b", "side", first)
    divergent = _commit(repo, "side.txt")
    _git(repo, "checkout", "-q", "-")
    receipt = _green_receipt(divergent, "demo-repo")

    with pytest.raises(AcceptanceError):
        accept_candidate(
            repo,
            "demo-repo",
            receipt,
            expected_parent_sha=second,
            milestone_id="M-001",
        )
    assert get_accepted_baseline(repo, "demo-repo") == second


def test_accept_candidate_raises_if_milestone_write_fails_after_baseline_advances(
    tmp_path,
):
    repo = _init_repo(tmp_path / "repo")
    first = _git(repo, "rev-parse", "HEAD")
    second = _commit(repo, "b.txt")

    # Pre-record the milestone id against a different commit so the
    # acceptance's milestone write is guaranteed to conflict.
    conflicting = _commit(repo, "unrelated.txt")
    create_milestone(repo, "M-001", conflicting)
    _git(repo, "reset", "-q", "--hard", second)

    receipt = _green_receipt(second, "demo-repo")
    with pytest.raises(AcceptanceError):
        accept_candidate(
            repo,
            "demo-repo",
            receipt,
            expected_parent_sha=None,
            milestone_id="M-001",
        )
    # The baseline still advanced even though the milestone write failed --
    # documented, observable partial-failure behavior the caller must check.
    assert get_accepted_baseline(repo, "demo-repo") == second
