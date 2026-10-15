"""IG-034 recovery classification: real Git refs and durable SQLite intents."""

from pathlib import Path

import pytest

from project_orchestrator.local_acceptance_recovery import classify_pending_acceptances
from project_orchestrator.local_first_state import LocalFirstState
from project_orchestrator.local_git_refs import LocalGitRefs
from test_local_git_refs import git, next_commit


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
    (owner / "human-mode.json").write_text('{"enabled":false}')
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


def test_missing_owner_marker_vetoes_recovery(recovery):
    refs, state, owner, base, target = recovery
    (owner / "human-mode.json").unlink()
    with pytest.raises(ValueError, match="OWNER_CONTROL_VETO"):
        classify_pending_acceptances(state=state, refs=refs)


def test_symlink_owner_marker_vetoes_recovery(recovery, tmp_path: Path):
    refs, state, owner, base, target = recovery
    marker = owner / "human-mode.json"
    alternate = tmp_path / "alternate-owner.json"
    alternate.write_text('{"enabled":false}')
    marker.unlink()
    try:
        marker.symlink_to(alternate)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks unavailable on this host")
    with pytest.raises(ValueError, match="OWNER_CONTROL_VETO"):
        classify_pending_acceptances(state=state, refs=refs)


def test_directory_owner_marker_vetoes_recovery(recovery):
    refs, state, owner, base, target = recovery
    marker = owner / "human-mode.json"
    marker.unlink()
    marker.mkdir()
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
    git(refs.repo, "update-ref", "-d", "refs/iron-god/accepted", base)
    refs.protect("milestone", "m1", target)
    assert classify_pending_acceptances(state=state, refs=refs)[0].disposition == (
        "ACCEPTED_REF_MISSING_STOP"
    )
    assert len(state.pending_acceptances()) == 1
