"""IG-034 evidence-gated acceptance transaction and crash replay."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from project_orchestrator.local_acceptance import (
    AcceptanceError,
    commit_or_recover_prepared_acceptance,
    prepare_verified_acceptance,
)
from project_orchestrator.local_first_state import LocalFirstState
from project_orchestrator.local_git_refs import LocalGitRefs
from project_orchestrator.local_verification import VerificationPolicy


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    ).stdout.strip()


@pytest.fixture
def case(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Iron God")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "code.py").write_text("value = 1\n", encoding="utf8")
    git(repo, "add", "code.py")
    git(repo, "commit", "-qm", "baseline")
    base = git(repo, "rev-parse", "HEAD")

    owner = tmp_path / "flow-state"
    owner.mkdir()
    (owner / "human-mode.json").write_text('{"enabled":false}', encoding="utf8")
    refs = LocalGitRefs(repo, owner_state_root=owner)
    refs.bootstrap_accepted(base)

    (repo / "code.py").write_text("value = 2\n", encoding="utf8")
    git(repo, "add", "code.py")
    git(repo, "commit", "-qm", "candidate")
    target = git(repo, "rev-parse", "HEAD")

    policy = VerificationPolicy(
        repository="jweter/Test",
        required_checks=("format", "tests"),
        product_reality_required=False,
    )
    payload = {
        "schema_version": 3,
        "repository": "jweter/Test",
        "head_sha": target,
        "mode": "FULL",
        "status": "GREEN",
        "active_check": None,
        "results": [
            {
                "name": "format",
                "returncode": 0,
                "timed_out": False,
                "interrupted": False,
            },
            {
                "name": "tests",
                "returncode": 0,
                "timed_out": False,
                "interrupted": False,
            },
        ],
    }
    evidence = tmp_path / "preflight-evidence.json"
    evidence.write_text(json.dumps(payload), encoding="utf8")
    state = LocalFirstState(tmp_path / "state.sqlite3")
    return repo, owner, refs, state, base, target, evidence, policy, payload


def prepare(case):
    repo, owner, refs, state, base, target, evidence, policy, payload = case
    return prepare_verified_acceptance(
        repo=repo,
        state=state,
        refs=refs,
        milestone_id="m1",
        accepted_base_sha=base,
        target_sha=target,
        evidence_path=evidence,
        policy=policy,
    )


def commit(case):
    repo, owner, refs, state, base, target, evidence, policy, payload = case
    return commit_or_recover_prepared_acceptance(
        repo=repo,
        state=state,
        refs=refs,
        milestone_id="m1",
        evidence_path=evidence,
        policy=policy,
    )


def test_prepare_protects_target_without_advancing_accepted(case):
    receipt = prepare(case)
    _, _, refs, state, base, target, _, _, _ = case
    assert receipt.head_sha == target
    assert refs.get("milestone", "m1") == target
    assert refs.accepted() == base
    assert state.acceptance_intent("m1")["state"] == "PREPARED"


def test_commit_revalidates_advances_and_finalizes(case):
    prepare(case)
    receipt = commit(case)
    _, _, refs, state, _, target, _, _, _ = case
    assert receipt.head_sha == target
    assert refs.accepted() == target
    assert state.acceptance_intent("m1")["state"] == "COMMITTED"
    assert state.pending_acceptances() == []


def test_crash_after_git_cas_replays_to_sqlite_finalize(case):
    prepare(case)
    _, _, refs, state, base, target, _, _, _ = case
    refs.advance_accepted(expected_sha=base, new_sha=target)
    assert state.acceptance_intent("m1")["state"] == "PREPARED"
    commit(case)
    assert refs.accepted() == target
    assert state.acceptance_intent("m1")["state"] == "COMMITTED"


def test_receipt_bytes_are_pinned_across_prepare_and_commit(case):
    prepare(case)
    *_, evidence, _, payload = case
    evidence.write_text(json.dumps(payload, indent=2), encoding="utf8")
    with pytest.raises(AcceptanceError, match="VERIFICATION_RECEIPT_CHANGED"):
        commit(case)
    _, _, refs, state, base, _, _, _, _ = case
    assert refs.accepted() == base
    assert state.acceptance_intent("m1")["state"] == "PREPARED"


def test_product_reality_required_fails_before_prepare(case):
    repo, owner, refs, state, base, target, evidence, _, _ = case
    policy = VerificationPolicy(
        repository="jweter/Test",
        required_checks=("format", "tests"),
        product_reality_required=True,
    )
    with pytest.raises(AcceptanceError, match="PRODUCT_REALITY_ADAPTER_REQUIRED"):
        prepare_verified_acceptance(
            repo=repo,
            state=state,
            refs=refs,
            milestone_id="m1",
            accepted_base_sha=base,
            target_sha=target,
            evidence_path=evidence,
            policy=policy,
        )
    assert refs.get("milestone", "m1") is None
    assert state.acceptance_intent("m1") is None


def test_owner_takeover_between_prepare_and_commit_stops(case):
    prepare(case)
    _, owner, refs, state, base, _, _, _, _ = case
    (owner / "human-mode.json").write_text('{"enabled":true}', encoding="utf8")
    with pytest.raises(AcceptanceError, match="OWNER_CONTROL_VETO"):
        commit(case)
    assert refs.accepted() == base
    assert state.acceptance_intent("m1")["state"] == "PREPARED"


def test_linked_integration_worktree_can_verify_and_commit(case):
    repo, owner, refs, state, base, target, evidence, policy, payload = case
    worktree = repo.parent / "integration-worktree"
    git(repo, "worktree", "add", "--detach", "--", str(worktree), target)
    prepare_verified_acceptance(
        repo=worktree,
        state=state,
        refs=refs,
        milestone_id="m1",
        accepted_base_sha=base,
        target_sha=target,
        evidence_path=evidence,
        policy=policy,
    )
    commit_or_recover_prepared_acceptance(
        repo=worktree,
        state=state,
        refs=refs,
        milestone_id="m1",
        evidence_path=evidence,
        policy=policy,
    )
    assert refs.accepted() == target
    assert state.acceptance_intent("m1")["state"] == "COMMITTED"


def test_unrelated_repository_cannot_use_ref_store(case):
    repo, owner, refs, state, base, target, evidence, policy, payload = case
    other = repo.parent / "unrelated"
    other.mkdir()
    git(other, "init", "-q")
    with pytest.raises(AcceptanceError, match="REPOSITORY_REF_STORE_MISMATCH"):
        prepare_verified_acceptance(
            repo=other,
            state=state,
            refs=refs,
            milestone_id="m1",
            accepted_base_sha=base,
            target_sha=target,
            evidence_path=evidence,
            policy=policy,
        )
