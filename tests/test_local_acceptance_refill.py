"""Real disposable-Git + SQLite A->B acceptance-refill canary.

No production refs, controller mode, workstation owner marker, or builders.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from project_orchestrator.local_acceptance import (
    commit_or_recover_prepared_acceptance,
    prepare_verified_acceptance,
)
from project_orchestrator.local_acceptance_refill import (
    select_next_after_acceptance,
)
from project_orchestrator.local_first_state import LocalFirstState
from project_orchestrator.local_git_refs import LocalGitRefs
from project_orchestrator.local_verification import VerificationPolicy
from tests.owner_authority import grant_owner_authority, revoke_owner_authority


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=True,
        timeout=20,
    ).stdout.strip()


@pytest.fixture
def demo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Iron God Test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "feature_a.py").write_text("value = 1\n", encoding="utf-8")
    git(repo, "add", "feature_a.py")
    git(repo, "commit", "-qm", "baseline")
    base = git(repo, "rev-parse", "HEAD")

    owner = tmp_path / "flow-state"
    owner.mkdir()
    grant_owner_authority(owner)
    refs = LocalGitRefs(repo, owner_state_root=owner)
    refs.bootstrap_accepted(base)

    (repo / "feature_a.py").write_text("value = 2\n", encoding="utf-8")
    git(repo, "add", "feature_a.py")
    git(repo, "commit", "-qm", "feature A")
    sha = git(repo, "rev-parse", "HEAD")
    evidence = tmp_path / "preflight-evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "repository": "jweter/Test",
                "head_sha": sha,
                "mode": "FULL",
                "status": "GREEN",
                "active_check": None,
                "results": [
                    {
                        "name": name,
                        "returncode": 0,
                        "timed_out": False,
                        "interrupted": False,
                    }
                    for name in ("format", "tests")
                ],
            }
        ),
        encoding="utf-8",
    )
    state = LocalFirstState(tmp_path / "state.sqlite3")
    policy = VerificationPolicy(
        repository="jweter/Test",
        required_checks=("format", "tests"),
        product_reality_required=False,
    )
    packet = {
        "slice_id": "IG-B",
        "state": "ELIGIBLE",
        "repository": "jweter/Test",
        "exact_base_sha": sha,
        "requires_milestones": ["feature-A"],
        "worktree_name": "ig-b-worker",
        "branch": "iron-god/feature-B",
    }
    controller = {
        "mode": "candidate_mode",
        "candidate_execution_authorized": True,
        "slices": [
            {"id": "IG-A", "state": "MERGED_TO_MAIN"},
            {"id": "IG-B", "state": "ELIGIBLE"},
        ],
    }
    return repo, owner, refs, state, base, sha, evidence, policy, controller, packet


def plan(demo, **overrides):
    _, owner, refs, state, _, _, _, _, controller, packet = demo
    args = dict(
        state=state,
        refs=refs,
        owner_state_root=owner,
        milestone_id="feature-A",
        controller=controller,
        handoffs={"IG-B": packet},
    )
    args.update(overrides)
    return select_next_after_acceptance(**args)


def accept_feature_a(demo):
    repo, _, refs, state, base, sha, evidence, policy, _, _ = demo
    prepare_verified_acceptance(
        repo=repo,
        state=state,
        refs=refs,
        milestone_id="feature-A",
        accepted_base_sha=base,
        target_sha=sha,
        evidence_path=evidence,
        policy=policy,
    )
    return commit_or_recover_prepared_acceptance(
        repo=repo,
        state=state,
        refs=refs,
        milestone_id="feature-A",
        evidence_path=evidence,
        policy=policy,
    )


def test_real_git_acceptance_emits_immediate_feature_b_handoff(demo):
    assert plan(demo).status == "HELD"
    receipt = accept_feature_a(demo)
    assert receipt.product_reality == "NOT_REQUIRED"
    result = plan(demo)
    assert result.status == "READY_FOR_LEASED_DISPATCH"
    assert result.next_slice_id == "IG-B"
    assert result.accepted_sha == demo[5]
    snapshot = demo[3].snapshot()
    assert result.acceptance_event_id == next(
        row["event_id"]
        for row in snapshot["journal"]
        if row["event_type"] == "MILESTONE_ACCEPTED"
    )
    # Repeatable observation: event identity never changes, even on restart.
    reopened = LocalFirstState(demo[3].path)
    assert plan(demo, state=reopened) == result


def test_shadow_mode_cannot_refill(demo):
    accept_feature_a(demo)
    ctl = dict(demo[8], mode="shadow_state_only")
    result = plan(demo, controller=ctl)
    assert result.status == "HELD"
    assert result.reason == "CANDIDATE_EXECUTION_NOT_AUTHORIZED"


def test_candidate_mode_without_explicit_authority_cannot_refill(demo):
    accept_feature_a(demo)
    ctl = dict(demo[8], candidate_execution_authorized=False)
    assert plan(demo, controller=ctl).status == "HELD"


def test_owner_takeover_cannot_refill(demo):
    accept_feature_a(demo)
    revoke_owner_authority(demo[1])
    result = plan(demo)
    assert result.reason == "OWNER_CONTROL_VETO"
    assert result.next_slice_id is None


def test_packet_without_registered_ledger_row_is_not_authoritative(demo):
    accept_feature_a(demo)
    ctl = dict(demo[8], slices=[{"id": "IG-A", "state": "MERGED_TO_MAIN"}])
    assert plan(demo, controller=ctl).status == "IDLE"


@pytest.mark.parametrize(
    "change",
    [
        {"exact_base_sha": "b" * 40},
        {"exact_base_sha": None},
        {"requires_milestones": ["not-committed"]},
        {"state": "BUILDING"},
        {"repository": "jweter/Wrong"},
        {"worktree_name": ""},
        {"branch": ""},
    ],
)
def test_invalid_or_dependency_blocked_packet_cannot_refill(demo, change):
    accept_feature_a(demo)
    packet = dict(demo[9], **change)
    assert plan(demo, handoffs={"IG-B": packet}).status == "IDLE"


def test_duplicate_controller_slice_ids_fail_closed(demo):
    accept_feature_a(demo)
    ctl = dict(
        demo[8],
        slices=[
            {"id": "IG-B", "state": "ELIGIBLE"},
            {"id": "IG-B", "state": "ELIGIBLE"},
        ],
    )
    assert plan(demo, controller=ctl).reason == "DUPLICATE_CONTROLLER_SLICE"


def test_active_slice_cannot_be_dispatched_twice(demo):
    accept_feature_a(demo)
    assert plan(demo, active_slice_ids=["IG-B"]).status == "IDLE"


def test_old_accepted_event_cannot_dispatch_after_new_baseline(demo):
    accept_feature_a(demo)
    repo, _, refs, _, _, _, _, _, _, _ = demo
    (repo / "feature_c.py").write_text("next = 1\n")
    git(repo, "add", "feature_c.py")
    git(repo, "commit", "-qm", "next baseline")
    refs.advance_accepted(expected_sha=demo[5], new_sha=git(repo, "rev-parse", "HEAD"))
    assert plan(demo).reason == "PROTECTED_ACCEPTED_REFS_NOT_AT_TARGET"


def test_prepared_but_uncommitted_intent_never_refills(demo):
    repo, _, refs, state, base, sha, evidence, policy, _, _ = demo
    prepare_verified_acceptance(
        repo=repo,
        state=state,
        refs=refs,
        milestone_id="feature-A",
        accepted_base_sha=base,
        target_sha=sha,
        evidence_path=evidence,
        policy=policy,
    )
    assert plan(demo).reason == "ACCEPTANCE_NOT_DURABLY_COMMITTED"
