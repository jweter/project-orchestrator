"""Integration staging tests with real disposable Git repositories."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from project_orchestrator.local_git_refs import LocalGitRefs
from project_orchestrator.local_integration import (
    IntegrationError,
    LocalIntegrationController,
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.strip()


def commit(repo: Path, name: str, body: str) -> str:
    (repo / name).write_text(body, encoding="utf8")
    git(repo, "add", "--", name)
    git(repo, "commit", "-qm", name)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repository(tmp_path: Path):
    repo = tmp_path / "src"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Iron God Test")
    git(repo, "config", "user.email", "iron-god-test@example.invalid")
    baseline = commit(repo, "base.txt", "baseline\n")
    owner = tmp_path / "owner"
    owner.mkdir()
    (owner / "human-mode.json").write_text('{"enabled":false}')
    refs = LocalGitRefs(repo, owner_state_root=owner)
    assert refs.bootstrap_accepted(baseline)
    controller = LocalIntegrationController(
        repo,
        workspace_root=tmp_path / "integrations",
        owner_state_root=owner,
    )
    return repo, owner, refs, controller, baseline


def candidate(repo: Path, refs: LocalGitRefs, name: str, filename: str):
    start = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-qb", name)
    new = commit(repo, filename, f"content of {name}\n")
    refs.protect("candidate", name, new)
    git(repo, "checkout", "-q", "--detach", start)
    return new


def test_stage_isolated_clean_merge_retains_all_sources(repository):
    repo, owner, refs, engine, baseline = repository
    cand = candidate(repo, refs, "one", "feature1.txt")
    stage = engine.stage(
        integration_id="int-one",
        candidate_id="one",
        candidate_base_sha=baseline,
        expected_accepted_sha=baseline,
    )
    assert stage.candidate_sha == cand
    assert stage.accepted_base_sha == baseline
    assert stage.integrated_sha not in (cand, baseline)
    assert (stage.worktree / "feature1.txt").read_text() == "content of one\n"
    assert git(repo, "rev-parse", "HEAD") == baseline
    assert refs.accepted() == baseline
    assert git(repo, "rev-parse", stage.recovery_ref) == stage.integrated_sha
    assert git(repo, "status", "--porcelain", "-uno") == ""


def test_two_independent_candidates_stage_on_advanced_accepted(repository):
    repo, _, refs, engine, baseline = repository
    a = candidate(repo, refs, "alice", "alice.txt")
    b = candidate(repo, refs, "bob", "bob.txt")
    stage_a = engine.stage(
        integration_id="int-alice",
        candidate_id="alice",
        candidate_base_sha=baseline,
        expected_accepted_sha=baseline,
    )
    refs.advance_accepted(expected_sha=baseline, new_sha=stage_a.integrated_sha)
    stage_b = engine.stage(
        integration_id="int-bob",
        candidate_id="bob",
        candidate_base_sha=baseline,
        expected_accepted_sha=stage_a.integrated_sha,
    )
    assert (stage_b.worktree / "alice.txt").exists()
    assert (stage_b.worktree / "bob.txt").exists()
    assert refs.accepted() == stage_a.integrated_sha
    assert a != b


def test_conflicts_fail_before_worktree_creation(repository):
    repo, _, refs, engine, baseline = repository
    candidate(repo, refs, "alice", "shared.txt")
    candidate(repo, refs, "bob", "shared.txt")
    stage_a = engine.stage(
        integration_id="int-alice",
        candidate_id="alice",
        candidate_base_sha=baseline,
        expected_accepted_sha=baseline,
    )
    refs.advance_accepted(expected_sha=baseline, new_sha=stage_a.integrated_sha)
    with pytest.raises(IntegrationError, match="INTEGRATION_CONFLICT"):
        engine.stage(
            integration_id="int-bob",
            candidate_id="bob",
            candidate_base_sha=baseline,
            expected_accepted_sha=stage_a.integrated_sha,
        )
    assert not (engine.workspace_root / "int-bob").exists()
    assert refs.accepted() == stage_a.integrated_sha
    assert refs.get("candidate", "bob") is not None


def test_existing_target_fails_without_overwrite(repository):
    repo, _, refs, engine, baseline = repository
    candidate(repo, refs, "one", "one.txt")
    target = engine.workspace_root / "int-one"
    target.mkdir(parents=True)
    (target / "personal-data.txt").write_text("preserve")
    with pytest.raises(IntegrationError, match="INTEGRATION_WORKTREE_OCCUPIED"):
        engine.stage(
            integration_id="int-one",
            candidate_id="one",
            candidate_base_sha=baseline,
            expected_accepted_sha=baseline,
        )
    assert (target / "personal-data.txt").read_text() == "preserve"


def test_owner_take_wheel_prevents_staging(repository):
    repo, owner, refs, engine, baseline = repository
    candidate(repo, refs, "one", "one.txt")
    (owner / "human-mode.json").write_text('{"enabled":true}')
    with pytest.raises(IntegrationError, match="OWNER_CONTROL_VETO"):
        engine.stage(
            integration_id="int-one",
            candidate_id="one",
            candidate_base_sha=baseline,
            expected_accepted_sha=baseline,
        )
    assert not engine.workspace_root.exists()


def test_wrong_accepted_sha_and_candidate_base_fail(repository):
    repo, _, refs, engine, baseline = repository
    new = candidate(repo, refs, "one", "one.txt")
    with pytest.raises(IntegrationError, match="ACCEPTED_BASE_CHANGED"):
        engine.stage(
            integration_id="int-one",
            candidate_id="one",
            candidate_base_sha=baseline,
            expected_accepted_sha=new,
        )
    with pytest.raises(IntegrationError, match="CANDIDATE_BASE_NOT_SHARED"):
        engine.stage(
            integration_id="int-one",
            candidate_id="one",
            candidate_base_sha="f" * 40,
            expected_accepted_sha=baseline,
        )


def test_missing_candidate_and_invalid_id_fail_closed(repository):
    _, _, _, engine, baseline = repository
    with pytest.raises(IntegrationError, match="UNPROTECTED_CANDIDATE"):
        engine.stage(
            integration_id="int-one",
            candidate_id="missing",
            candidate_base_sha=baseline,
            expected_accepted_sha=baseline,
        )
    with pytest.raises(IntegrationError, match="INVALID_INTEGRATION_ID"):
        engine.stage(
            integration_id="../escape",
            candidate_id="missing",
            candidate_base_sha=baseline,
            expected_accepted_sha=baseline,
        )


def test_dirty_original_checkout_remains_untouched(repository):
    repo, _, refs, engine, baseline = repository
    candidate(repo, refs, "one", "one.txt")
    (repo / "base.txt").write_text("uncommitted user work\n")
    stage = engine.stage(
        integration_id="int-one",
        candidate_id="one",
        candidate_base_sha=baseline,
        expected_accepted_sha=baseline,
    )
    assert (repo / "base.txt").read_text() == "uncommitted user work\n"
    assert (stage.worktree / "base.txt").read_text() == "baseline\n"


def test_same_integration_id_never_reused(repository):
    repo, _, refs, engine, baseline = repository
    candidate(repo, refs, "one", "one.txt")
    engine.stage(
        integration_id="int-one",
        candidate_id="one",
        candidate_base_sha=baseline,
        expected_accepted_sha=baseline,
    )
    with pytest.raises(IntegrationError, match="INTEGRATION_ID_ALREADY_PROTECTED"):
        engine.stage(
            integration_id="int-one",
            candidate_id="one",
            candidate_base_sha=baseline,
            expected_accepted_sha=baseline,
        )
