import shutil
"""IG-034 recovery classification: real Git refs and durable SQLite intents."""

from pathlib import Path

import pytest

from project_orchestrator.local_acceptance_recovery import classify_pending_acceptances
from project_orchestrator.local_first_state import LocalFirstState
from project_orchestrator.local_git_refs import BASELINE_REF, LocalGitRefs
from test_local_git_refs import git, next_commit
from tests.owner_authority import (
    UNSAFE_OWNER_SCENARIOS,
    apply_owner_scenario,
    grant_owner_authority,
    revoke_owner_authority,
    tamper_owner_authority,
)
from project_orchestrator.iron_god_owner_control import control_root


@pytest.fixture
def recovery(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Iron God Test")
    git(repo, "config", "user.email", "iron-god-test@example.invalid")
    (repo / "source.txt").write_text("initial\n")
    git(repo, "add", "--", "source.txt")
    git(repo, "commit", "-qm", "initial")
    owner = tmp_path / "flow-state"
    owner.mkdir()
    grant_owner_authority(owner)
    refs = LocalGitRefs(repo, owner_state_root=owner)
    base = git(repo, "rev-parse", "HEAD")
    refs.bootstrap_accepted(base)
    target = next_commit(repo, "next\n")
    state = LocalFirstState(tmp_path / "state.sqlite3")
    state.prepare_acceptance(
        repository="jweter/Test",
        milestone_id="m1",
        accepted_base_sha=base,
        accepted_sha=target,
        verification_sha256="f" * 64,
        product_reality="NOT_REQUIRED",
        context_json="{}",
    )
    return refs, state, owner, base, target


def test_unadvanced_cas_requires_revalidation(recovery):
    refs, state, owner, base, target = recovery
    result = classify_pending_acceptances(state=state, refs=refs)
    assert [(r.milestone_id, r.disposition) for r in result] == [
        ("m1", "MILESTONE_REF_MISSING_STOP")
    ]
    assert refs.accepted() == base
    assert len(state.pending_acceptances()) == 1


def test_advanced_cas_requires_receipt_revalidation(recovery):
    refs, state, owner, base, target = recovery
    refs.protect("milestone", "m1", target)
    refs.advance_accepted(expected_sha=base, new_sha=target)
    result = classify_pending_acceptances(state=state, refs=refs)
    assert result[0].disposition == "CAS_DONE_REVALIDATE_BEFORE_FINALIZE"
    assert len(state.pending_acceptances()) == 1
    assert state.snapshot()["milestones"] == []


def test_conflicting_milestone_ref_stops(recovery):
    refs, state, owner, base, target = recovery
    refs.protect("milestone", "m1", base)
    assert classify_pending_acceptances(state=state, refs=refs)[0].disposition == (
        "MILESTONE_REF_CONFLICT"
    )


def test_missing_owner_controller_state_vetoes_recovery(recovery):
    refs, state, owner, base, target = recovery
    shutil.rmtree(control_root(owner))
    with pytest.raises(ValueError, match="OWNER_CONTROL_VETO"):
        classify_pending_acceptances(state=state, refs=refs)


def test_tampered_owner_controller_vetoes_recovery(recovery):
    refs, state, owner, base, target = recovery
    tamper_owner_authority(owner)
    with pytest.raises(ValueError, match="OWNER_CONTROL_VETO"):
        classify_pending_acceptances(state=state, refs=refs)


def test_revoked_owner_controller_vetoes_recovery(recovery):
    refs, state, owner, base, target = recovery
    revoke_owner_authority(owner)
    with pytest.raises(ValueError, match="OWNER_CONTROL_VETO"):
        classify_pending_acceptances(state=state, refs=refs)


def test_prepared_with_protected_milestone_is_ready_for_cas(recovery):
    refs, state, owner, base, target = recovery
    refs.protect("milestone", "m1", target)
    assert classify_pending_acceptances(state=state, refs=refs)[0].disposition == (
        "CAS_NOT_DONE_REVALIDATE_BEFORE_ADVANCE"
    )
    assert refs.accepted() == base


def test_advanced_cas_without_protected_milestone_stops(recovery):
    refs, state, owner, base, target = recovery
    refs.advance_accepted(expected_sha=base, new_sha=target)
    assert classify_pending_acceptances(state=state, refs=refs)[0].disposition == (
        "MILESTONE_REF_MISSING_STOP"
    )


def test_missing_accepted_baseline_stops_recovery(recovery):
    refs, state, owner, base, target = recovery
    git(refs.repo, "update-ref", "-d", BASELINE_REF, base)
    refs.protect("milestone", "m1", target)
    assert classify_pending_acceptances(state=state, refs=refs)[0].disposition == (
        "ACCEPTED_REF_MISSING_STOP"
    )
    assert len(state.pending_acceptances()) == 1
