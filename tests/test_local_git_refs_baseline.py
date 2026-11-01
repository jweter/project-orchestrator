"""The cumulative baseline lives where it cannot collide with the lane's per-packet anchors (#597)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from project_orchestrator import iron_god_ref_protection as protection
from project_orchestrator.local_git_refs import BASELINE_REF, LEGACY_BASELINE_REF, LocalGitRefError, LocalGitRefs
from tests.owner_authority import grant_owner_authority

PACKET_ANCHOR = "refs/iron-god/accepted/product-voicestudio-20261101t000000z"


def git(path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, check=check, timeout=30)


@pytest.fixture
def repo(tmp_path: Path):
    working = tmp_path / "repo"
    working.mkdir()
    git(working, "init", "-q")
    git(working, "config", "user.name", "Iron God Test")
    git(working, "config", "user.email", "iron-god-test@example.invalid")
    shas = []
    for number in range(3):
        (working / "f.txt").write_text(f"v{number}\n", encoding="utf-8")
        git(working, "add", "--", "f.txt")
        git(working, "commit", "-qm", f"c{number}")
        shas.append(git(working, "rev-parse", "HEAD").stdout.strip())
    control = tmp_path / "flow-state"
    control.mkdir()
    grant_owner_authority(control)
    return working, LocalGitRefs(working, owner_state_root=control), shas


def test_the_baseline_and_a_lane_anchor_now_coexist_in_one_repository_in_either_order(repo) -> None:
    """The #597 collision: Git refused the second of these in either order."""
    working, refs, (first, second, _third) = repo
    git(working, "update-ref", PACKET_ANCHOR, first)  # the lane's anchor first ...
    assert refs.bootstrap_accepted(first) is True  # ... then the baseline

    assert git(working, "rev-parse", BASELINE_REF).stdout.strip() == first and refs.accepted() == first
    assert git(working, "rev-parse", PACKET_ANCHOR).stdout.strip() == first

    other = working.parent / "other"
    other.mkdir()
    git(other, "init", "-q")
    git(other, "config", "user.name", "x")
    git(other, "config", "user.email", "x@y")
    (other / "f.txt").write_text("x\n", encoding="utf-8")
    git(other, "add", "--", "f.txt")
    git(other, "commit", "-qm", "c")
    sha = git(other, "rev-parse", "HEAD").stdout.strip()
    control = working.parent / "flow-state"
    reversed_refs = LocalGitRefs(other, owner_state_root=control)
    assert reversed_refs.bootstrap_accepted(sha) is True  # the baseline first ...
    git(other, "update-ref", PACKET_ANCHOR, sha)  # ... then the lane's anchor
    assert git(other, "rev-parse", PACKET_ANCHOR).stdout.strip() == sha


def test_new_writes_never_touch_the_legacy_name(repo) -> None:
    working, refs, (first, second, _third) = repo
    refs.bootstrap_accepted(first)
    refs.advance_accepted(expected_sha=first, new_sha=second)

    assert git(working, "rev-parse", BASELINE_REF).stdout.strip() == second
    assert git(working, "rev-parse", "--verify", "--quiet", LEGACY_BASELINE_REF, check=False).returncode != 0
    assert refs.legacy_baseline() is None


def test_a_repository_that_only_has_the_legacy_baseline_keeps_working_and_is_never_modified(repo) -> None:
    """The preserved KE-Core worker repositories hold the legacy ref; they must still read and advance."""
    working, refs, (first, second, third) = repo
    git(working, "update-ref", LEGACY_BASELINE_REF, first)

    assert refs.accepted() == first and refs.legacy_baseline() == first
    assert refs.bootstrap_accepted(first) is False  # already initialized to this commit
    with pytest.raises(LocalGitRefError, match="ACCEPTED_ALREADY_INITIALIZED"):
        refs.bootstrap_accepted(second)
    refs.advance_accepted(expected_sha=first, new_sha=second)

    assert refs.accepted() == second  # the new ref now leads ...
    assert git(working, "rev-parse", BASELINE_REF).stdout.strip() == second
    assert git(working, "rev-parse", LEGACY_BASELINE_REF).stdout.strip() == first  # ... and the legacy ref is untouched
    refs.advance_accepted(expected_sha=second, new_sha=third)
    assert git(working, "rev-parse", LEGACY_BASELINE_REF).stdout.strip() == first


def test_the_compare_and_swap_still_rejects_a_stale_or_non_fast_forward_advance(repo) -> None:
    working, refs, (first, second, third) = repo
    refs.bootstrap_accepted(second)

    with pytest.raises(LocalGitRefError, match="ACCEPTED_BASE_CHANGED"):
        refs.advance_accepted(expected_sha=first, new_sha=third)
    with pytest.raises(LocalGitRefError, match="NON_FAST_FORWARD_ACCEPTANCE"):
        refs.advance_accepted(expected_sha=second, new_sha=first)
    assert refs.accepted() == second


def test_the_new_baseline_is_inside_the_protection_policy_and_cannot_be_deleted_or_forced() -> None:
    assert protection.is_protected_ref(BASELINE_REF) and protection.is_no_delete_ref(BASELINE_REF)
    assert protection.check_ref_operation(BASELINE_REF, "delete").allowed is False
    assert protection.check_ref_operation(BASELINE_REF, "force-update").allowed is False
    assert protection.check_ref_operation(BASELINE_REF, "fast-forward").allowed is True
