from __future__ import annotations

import json
import subprocess
from pathlib import Path

from project_orchestrator.iron_god_candidate_refs import set_candidate
from project_orchestrator.iron_god_milestone_refs import create_milestone
from project_orchestrator.iron_god_recovery_reconstruction_report import (
    RecoveryReconstructionReport,
    SliceReconstructionRequest,
    build_recovery_reconstruction_report,
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
    (path / "README.md").write_text("root\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "init")
    return path


def _commit(path: Path, name: str) -> str:
    (path / name).write_text("x\n", encoding="utf-8")
    _git(path, "add", name)
    _git(path, "commit", "-q", "-m", name)
    return _git(path, "rev-parse", "HEAD")


def _authorized_state_root(tmp_path: Path) -> Path:
    state_root = tmp_path / "flow-state"
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / "human-mode.json").write_text(
        json.dumps({"enabled": False}), encoding="utf-8"
    )
    return state_root


def _add_worktree(repo: Path, wt_path: Path, branch: str) -> Path:
    _git(repo, "worktree", "add", "-b", branch, str(wt_path), "HEAD")
    return wt_path


def test_clean_scenario_has_no_ambiguity_or_orphans(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    wt = _add_worktree(repo, tmp_path / "wt-known", "known-branch")

    report = build_recovery_reconstruction_report(
        repo,
        _authorized_state_root(tmp_path),
        (SliceReconstructionRequest(slice_id="slice-x", milestone_id="M-x"),),
        known_worktree_names=frozenset({repo.name, wt.name}),
    )

    assert report.clean is True
    assert report.has_ambiguous_slice is False
    assert report.has_orphaned_worktree is False
    assert report.slice_states[0].status == "NO_CANDIDATE"


def test_ambiguous_slice_is_surfaced_not_resolved(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    wt = _add_worktree(repo, tmp_path / "wt-known", "known-branch")

    first = _git(repo, "rev-parse", "HEAD")
    second = _commit(repo, "b.txt")
    set_candidate(repo, "slice-x", second, expected_current_sha=None)
    create_milestone(repo, "M-x", first)

    report = build_recovery_reconstruction_report(
        repo,
        _authorized_state_root(tmp_path),
        (SliceReconstructionRequest(slice_id="slice-x", milestone_id="M-x"),),
        known_worktree_names=frozenset({repo.name, wt.name}),
    )

    assert report.slice_states[0].status == "AMBIGUOUS"
    assert report.has_ambiguous_slice is True
    assert report.clean is False
    assert report.has_orphaned_worktree is False


def test_unregistered_worktree_is_orphaned_not_silently_dropped(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    _add_worktree(repo, tmp_path / "wt-stray", "stray-branch")

    report = build_recovery_reconstruction_report(
        repo,
        _authorized_state_root(tmp_path),
        (SliceReconstructionRequest(slice_id="slice-x", milestone_id="M-x"),),
        known_worktree_names=frozenset({repo.name}),
    )

    assert report.has_orphaned_worktree is True
    assert report.clean is False
    orphaned_names = {
        entry.worktree_name
        for entry in report.worktree_bootstrap
        if entry.classification == "ORPHANED"
    }
    assert "wt-stray" in orphaned_names


def test_fully_empty_report_is_not_clean():
    """2026-10-16 fail-closed regression: a report built from zero
    evidence (nothing requested, nothing scanned) must never report
    clean=True -- checking nothing is not the same as checking
    everything and finding it clean."""
    report = RecoveryReconstructionReport(
        slice_states=(), worktree_bootstrap=(), worktree_classifications=()
    )
    assert report.has_coverage is False
    assert report.clean is False


def test_zero_slice_requests_with_real_worktree_scan_is_not_clean(tmp_path):
    """Even when a real worktree scan ran, zero slice_requests means
    no candidate/milestone/accepted-baseline evidence was ever
    checked -- still incomplete, not clean."""
    repo = _init_repo(tmp_path / "repo")

    report = build_recovery_reconstruction_report(
        repo,
        _authorized_state_root(tmp_path),
        (),
        known_worktree_names=frozenset({repo.name}),
    )

    assert report.slice_states == ()
    assert report.has_coverage is False
    assert report.clean is False
