"""Real local-Git tests of Iron God commit survival and CAS controls."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from project_orchestrator.local_git_refs import LocalGitRefError, LocalGitRefs
from tests.owner_authority import (
    UNSAFE_OWNER_SCENARIOS,
    apply_owner_scenario,
    grant_owner_authority,
    revoke_owner_authority,
    tamper_owner_authority,
)


def git(path: Path, *args: str) -> str:
    p = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True, text=True, check=True, timeout=20,
    )
    return p.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, Path, LocalGitRefs]:
    working = tmp_path / "repo"
    working.mkdir()
    git(working, "init", "-q")
    git(working, "config", "user.name", "Iron God Test")
    git(working, "config", "user.email", "iron-god-test@example.invalid")
    (working / "source.txt").write_text("initial\n", encoding="utf-8")
    git(working, "add", "--", "source.txt")
    git(working, "commit", "-qm", "initial")
    control = tmp_path / "flow-state"
    control.mkdir()
    grant_owner_authority(control)
    return working, control, LocalGitRefs(working, owner_state_root=control)


def next_commit(path: Path, content: str) -> str:
    (path / "source.txt").write_text(content, encoding="utf-8")
    git(path, "add", "--", "source.txt")
    git(path, "commit", "-qm", "test update")
    return git(path, "rev-parse", "HEAD")


def test_immutable_candidate_and_milestone_refs(repo):
    working, owner, refs = repo
    head = git(working, "rev-parse", "HEAD")
    assert refs.protect("candidate", "slice-1", head)
    assert refs.protect("milestone", "milestone-1", head)
    assert refs.get("candidate", "slice-1") == head
    assert refs.protect("candidate", "slice-1", head) is False
    advanced = next_commit(working, "second\n")
    with pytest.raises(LocalGitRefError, match="PROTECTED_REF_CONFLICT"):
        refs.protect("candidate", "slice-1", advanced)
    assert refs.get("milestone", "milestone-1") == head
    assert refs.get("recovery", "missing") is None


def test_commit_reachable_after_temp_branch_deleted(repo):
    working, owner, refs = repo
    base = git(working, "rev-parse", "HEAD")
    git(working, "checkout", "-qb", "disposable")
    candidate = next_commit(working, "protected candidate\n")
    refs.protect("candidate", "slice-7", candidate)
    git(working, "checkout", "-q", "master" if git(working, "branch", "--list", "master") else "main")
    git(working, "branch", "-D", "disposable")
    git(working, "gc", "--prune=now")
    assert refs.get("candidate", "slice-7") == candidate
    assert git(working, "cat-file", "-t", candidate) == "commit"
    assert base != candidate


def test_accepted_fast_forward_and_stale_compare_and_swap(repo):
    working, owner, refs = repo
    old = git(working, "rev-parse", "HEAD")
    assert refs.bootstrap_accepted(old)
    assert refs.bootstrap_accepted(old) is False
    new = next_commit(working, "v2\n")
    refs.advance_accepted(expected_sha=old, new_sha=new)
    assert refs.accepted() == new
    with pytest.raises(LocalGitRefError, match="ACCEPTED_BASE_CHANGED"):
        refs.advance_accepted(expected_sha=old, new_sha=new)
    with pytest.raises(LocalGitRefError, match="ACCEPTED_ALREADY_INITIALIZED"):
        refs.bootstrap_accepted(old)


def test_non_fast_forward_cannot_replace_accepted(repo):
    working, owner, refs = repo
    base = git(working, "rev-parse", "HEAD")
    refs.bootstrap_accepted(base)
    new = next_commit(working, "v2\n")
    refs.advance_accepted(expected_sha=base, new_sha=new)
    with pytest.raises(LocalGitRefError, match="NON_FAST_FORWARD_ACCEPTANCE"):
        refs.advance_accepted(expected_sha=new, new_sha=base)
    assert refs.accepted() == new


@pytest.mark.parametrize("scenario", ["revoked", "tampered"])
def test_owner_veto_blocks_all_writes(repo, scenario):
    working, owner, refs = repo
    sha = git(working, "rev-parse", "HEAD")
    if scenario == "revoked":
        revoke_owner_authority(owner)
    else:
        tamper_owner_authority(owner)
    with pytest.raises(LocalGitRefError, match="OWNER_CONTROL_VETO"):
        refs.protect("candidate", "slice-1", sha)
    with pytest.raises(LocalGitRefError, match="OWNER_CONTROL_VETO"):
        refs.bootstrap_accepted(sha)
    assert refs.get("candidate", "slice-1") is None
    assert refs.accepted() is None


def test_take_wheel_vetoes_accepted_advancement(repo):
    working, owner, refs = repo
    old = git(working, "rev-parse", "HEAD")
    refs.bootstrap_accepted(old)
    new = next_commit(working, "change\n")
    revoke_owner_authority(owner)
    with pytest.raises(LocalGitRefError, match="OWNER_CONTROL_VETO"):
        refs.advance_accepted(expected_sha=old, new_sha=new)
    assert refs.accepted() == old


@pytest.mark.parametrize("refid", ["../escape", ".", ".lock", "aa..bb", "hello/evil", "x.lock"])
def test_bad_ids_rejected_without_writes(repo, refid):
    working, owner, refs = repo
    with pytest.raises(LocalGitRefError, match="INVALID_REF_IDENTIFIER"):
        refs.protect("candidate", refid, git(working, "rev-parse", "HEAD"))


def test_unknown_object_cannot_be_anchored(repo):
    working, owner, refs = repo
    with pytest.raises(LocalGitRefError, match="COMMIT_NOT_FOUND"):
        refs.protect("milestone", "m1", "a" * 40)


def test_invalid_class_cannot_create_ref(repo):
    working, owner, refs = repo
    with pytest.raises(LocalGitRefError, match="INVALID_REF_CLASS"):
        refs.protect("accepted", "oops", git(working, "rev-parse", "HEAD"))
