"""RDI-001 binding: no claim or worker until existing acceptance is verified."""
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from project_orchestrator import resident_dispatch_authority as authority
from project_orchestrator import resident_dispatch_binding as binding
from project_orchestrator.local_first_state import LocalFirstState


def arguments(tmp_path):
    # A pre-existing isolated worktree is required before a durable claim.
    work = tmp_path / "ig001-worker-1"
    work.mkdir()
    controller = {
        "controller": "iron-god-handoff-v1",
        "slices": [],
        "mode": "candidate_mode",
        "candidate_execution_authorized": True,
    }
    controller_state_path = tmp_path / "controller-handoff-state.json"
    controller_state_path.write_text(json.dumps(controller), encoding="utf-8")
    return dict(
        controller=controller,
        controller_state_path=controller_state_path,
        expected_controller_state_path=controller_state_path,
        owner_state_root=tmp_path,
        expected_owner_state_root=tmp_path,
        state_db=tmp_path / "accepted.sqlite",
        repo=tmp_path,
        milestone_id="M-001",
        handoffs={"IG-001": {"state": "ELIGIBLE"}},
        claims_path=tmp_path / "claims.sqlite",
        worktree_root=tmp_path,
        task={"id": "IG-001", "work": work},
        worker_executor=lambda **kwargs: kwargs,
    )


def test_shadow_refuses_before_state_or_claim_creation(tmp_path, monkeypatch):
    monkeypatch.setattr(authority, "owner_control_status", lambda root: authority.AUTHORIZED)
    args = arguments(tmp_path)
    args["controller"]["mode"] = "shadow_state_only"
    args["controller"]["candidate_execution_authorized"] = False
    with pytest.raises(binding.ResidentDispatchBlocked, match="CANDIDATE_EXECUTION_NOT_AUTHORIZED"):
        binding.dispatch_existing_accepted_event(**args)
    assert not args["state_db"].exists()
    assert not args["claims_path"].exists()


def test_forged_controller_flags_never_open_state_or_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(authority, "owner_control_status", lambda root: authority.AUTHORIZED)
    args = arguments(tmp_path)
    args["controller"]["controller"] = "untrusted"
    with pytest.raises(binding.ResidentDispatchBlocked, match="CONTROLLER_IDENTITY_UNVERIFIED"):
        binding.dispatch_existing_accepted_event(**args)
    assert not args["state_db"].exists()
    assert not args["claims_path"].exists()


def test_missing_accepted_ref_refuses_without_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(authority, "owner_control_status", lambda root: authority.AUTHORIZED)
    args = arguments(tmp_path)
    LocalFirstState(args["state_db"])
    monkeypatch.setattr(
        binding, "LocalGitRefs",
        lambda repo, *, owner_state_root: SimpleNamespace(accepted=lambda: None),
    )
    monkeypatch.setattr(
        binding, "LocalFirstState",
        lambda db: pytest.fail("missing accepted ref must not open writable state"),
    )
    with pytest.raises(binding.ResidentDispatchBlocked, match="ACCEPTED_BASELINE_MISSING"):
        binding.dispatch_existing_accepted_event(**args)
    assert not args["claims_path"].exists()


def test_reserved_worker_binds_exact_selector_state_and_refs(tmp_path, monkeypatch):
    monkeypatch.setattr(authority, "owner_control_status", lambda root: authority.AUTHORIZED)
    args = arguments(tmp_path)
    LocalFirstState(args["state_db"])
    refs = SimpleNamespace(accepted=lambda: "a" * 40)
    monkeypatch.setattr(
        binding, "LocalGitRefs", lambda repo, *, owner_state_root: refs
    )
    received = []
    marker = object()

    def coordinator(**kwargs):
        received.append(kwargs)
        return marker

    coordinator_module = ModuleType("accepted_event_dispatch_coordinator")
    coordinator_module.dispatch_one_accepted_event = coordinator
    monkeypatch.setitem(sys.modules, coordinator_module.__name__, coordinator_module)
    assert binding.dispatch_existing_accepted_event(**args) is marker
    assert len(received) == 1
    assert received[0]["refs"] is refs
    callback = received[0]["worker_executor"]
    assert callback is not args["worker_executor"]
    reserved_claim = object()
    selected_task = args["task"]
    bound = callback(reserved_claim, selected_task)
    assert bound["claim"] is reserved_claim
    assert bound["task"] is selected_task
    assert bound["state"] is received[0]["state"]
    assert bound["refs"] is received[0]["refs"]
    assert received[0]["controller"] is args["controller"]
    assert received[0]["milestone_id"] == args["milestone_id"]
    assert received[0]["task"] is args["task"]
    assert received[0]["claims_path"] == args["claims_path"]
    assert not args["claims_path"].exists()


def test_owner_takeover_after_state_open_prevents_coordinator(tmp_path, monkeypatch):
    args = arguments(tmp_path)
    LocalFirstState(args["state_db"])
    checks = iter([authority.AUTHORIZED, authority.AUTHORIZED, "BLOCKED"])
    monkeypatch.setattr(authority, "owner_control_status", lambda root: next(checks))
    monkeypatch.setattr(
        binding, "LocalGitRefs",
        lambda repo, *, owner_state_root: SimpleNamespace(accepted=lambda: "a" * 40),
    )
    with pytest.raises(binding.ResidentDispatchBlocked, match="OWNER_CONTROL_VETO"):
        binding.dispatch_existing_accepted_event(**args)
    assert not args["claims_path"].exists()


def test_invalid_milestone_fails_before_writable_state_open(tmp_path, monkeypatch):
    monkeypatch.setattr(authority, "owner_control_status", lambda root: authority.AUTHORIZED)
    args = arguments(tmp_path)
    LocalFirstState(args["state_db"])
    args["milestone_id"] = ""
    monkeypatch.setattr(
        binding, "LocalFirstState",
        lambda db: pytest.fail("state constructor must not run"),
    )
    with pytest.raises(binding.ResidentDispatchBlocked, match="ACCEPTED_MILESTONE_ID_MISSING"):
        binding.dispatch_existing_accepted_event(**args)
    assert not args["claims_path"].exists()


def test_owner_takeover_before_reserved_worker_invocation_fails_closed(tmp_path, monkeypatch):
    args = arguments(tmp_path)
    LocalFirstState(args["state_db"])
    owner = {"released": True}
    monkeypatch.setattr(
        authority, "owner_control_status",
        lambda root: authority.AUTHORIZED if owner["released"] else "BLOCKED",
    )
    monkeypatch.setattr(
        binding, "LocalGitRefs",
        lambda repo, *, owner_state_root: SimpleNamespace(accepted=lambda: "a" * 40),
    )
    args["worker_executor"] = lambda **kwargs: pytest.fail(
        "owner takeover must veto the reserved worker callback"
    )
    captured = []
    coordinator_module = ModuleType("accepted_event_dispatch_coordinator")
    coordinator_module.dispatch_one_accepted_event = lambda **kwargs: captured.append(kwargs)
    monkeypatch.setitem(sys.modules, coordinator_module.__name__, coordinator_module)
    binding.dispatch_existing_accepted_event(**args)
    assert len(captured) == 1
    owner["released"] = False
    with pytest.raises(binding.ResidentDispatchBlocked, match="OWNER_CONTROL_VETO"):
        captured[0]["worker_executor"](object(), args["task"])
    assert not args["claims_path"].exists()


def test_invalid_accepted_sha_never_opens_writable_state(tmp_path, monkeypatch):
    monkeypatch.setattr(authority, "owner_control_status", lambda root: authority.AUTHORIZED)
    args = arguments(tmp_path)
    LocalFirstState(args["state_db"])
    monkeypatch.setattr(
        binding, "LocalGitRefs",
        lambda repo, *, owner_state_root: SimpleNamespace(
            accepted=lambda: "not-a-commit"
        ),
    )
    monkeypatch.setattr(
        binding, "LocalFirstState",
        lambda db: pytest.fail("invalid accepted SHA must not open writable state"),
    )
    with pytest.raises(binding.ResidentDispatchBlocked, match="ACCEPTED_BASELINE_INVALID"):
        binding.dispatch_existing_accepted_event(**args)
    assert not args["claims_path"].exists()


def test_baseline_drift_during_state_open_prevents_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(authority, "owner_control_status", lambda root: authority.AUTHORIZED)
    args = arguments(tmp_path)
    LocalFirstState(args["state_db"])
    current = {"sha": "a" * 40}
    monkeypatch.setattr(
        binding, "LocalGitRefs",
        lambda repo, *, owner_state_root: SimpleNamespace(
            accepted=lambda: current["sha"]
        ),
    )

    def state_constructor(db):
        current["sha"] = "b" * 40
        return object()

    monkeypatch.setattr(binding, "LocalFirstState", state_constructor)
    with pytest.raises(binding.ResidentDispatchBlocked, match="ACCEPTED_BASELINE_CHANGED"):
        binding.dispatch_existing_accepted_event(**args)
    assert not args["claims_path"].exists()


def test_baseline_drift_after_reservation_vetoes_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(authority, "owner_control_status", lambda root: authority.AUTHORIZED)
    args = arguments(tmp_path)
    LocalFirstState(args["state_db"])
    current = {"sha": "a" * 40}
    monkeypatch.setattr(
        binding, "LocalGitRefs",
        lambda repo, *, owner_state_root: SimpleNamespace(
            accepted=lambda: current["sha"]
        ),
    )
    args["worker_executor"] = lambda **kwargs: pytest.fail(
        "stale accepted baseline must not invoke reserved worker"
    )
    captured = []
    coordinator_module = ModuleType("accepted_event_dispatch_coordinator")
    coordinator_module.dispatch_one_accepted_event = lambda **kwargs: captured.append(kwargs)
    monkeypatch.setitem(sys.modules, coordinator_module.__name__, coordinator_module)
    binding.dispatch_existing_accepted_event(**args)
    assert len(captured) == 1
    current["sha"] = "b" * 40
    with pytest.raises(binding.ResidentDispatchBlocked, match="ACCEPTED_BASELINE_CHANGED"):
        captured[0]["worker_executor"](object(), args["task"])
    assert not args["claims_path"].exists()


def test_missing_worktree_blocks_before_durable_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(authority, "owner_control_status", lambda root: authority.AUTHORIZED)
    args = arguments(tmp_path)
    LocalFirstState(args["state_db"])
    monkeypatch.setattr(
        binding, "LocalGitRefs",
        lambda *a, **kw: SimpleNamespace(accepted=lambda: "a" * 40),
    )
    args["task"]["work"] = tmp_path / "not-provisioned"
    coordinator_module = ModuleType("accepted_event_dispatch_coordinator")
    coordinator_module.dispatch_one_accepted_event = lambda **kw: pytest.fail(
        "missing worktree must not reach durable claim"
    )
    monkeypatch.setitem(sys.modules, coordinator_module.__name__, coordinator_module)
    with pytest.raises(binding.ResidentDispatchBlocked, match="DISPATCH_WORKTREE_NOT_READY"):
        binding.dispatch_existing_accepted_event(**args)
    assert not args["claims_path"].exists()


def test_worktree_outside_authorized_root_blocks_before_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(authority, "owner_control_status", lambda root: authority.AUTHORIZED)
    args = arguments(tmp_path)
    LocalFirstState(args["state_db"])
    monkeypatch.setattr(
        binding, "LocalGitRefs",
        lambda *a, **kw: SimpleNamespace(accepted=lambda: "a" * 40),
    )
    args["task"]["work"] = tmp_path.parent
    coordinator_module = ModuleType("accepted_event_dispatch_coordinator")
    coordinator_module.dispatch_one_accepted_event = lambda **kw: pytest.fail(
        "out-of-root worktree must not reach durable claim"
    )
    monkeypatch.setitem(sys.modules, coordinator_module.__name__, coordinator_module)
    with pytest.raises(binding.ResidentDispatchBlocked, match="DISPATCH_WORKTREE_NOT_READY"):
        binding.dispatch_existing_accepted_event(**args)
    assert not args["claims_path"].exists()


def test_implicit_milestone_and_task_derive_from_one_accepted_event(tmp_path, monkeypatch):
    from project_orchestrator import local_acceptance_refill

    monkeypatch.setattr(authority, "owner_control_status", lambda root: authority.AUTHORIZED)
    args = arguments(tmp_path)
    LocalFirstState(args["state_db"])
    sha = "a" * 40
    refs = SimpleNamespace(
        accepted=lambda: sha, get=lambda kind, ident: sha
    )
    snapshot = {
        "acceptance_intents": [{
            "milestone_id": "M-001", "state": "COMMITTED",
            "accepted_sha": sha, "repository": "orchestrator",
            "verification_sha256": "b" * 64,
        }],
        "milestones": [{
            "milestone_id": "M-001", "repository": "orchestrator",
            "accepted_sha": sha, "verification_sha256": "b" * 64,
        }],
        "journal": [{
            "milestone_id": "M-001", "event_type": "MILESTONE_ACCEPTED",
            "detail": sha,
        }],
    }
    state = SimpleNamespace(snapshot=lambda: snapshot)
    monkeypatch.setattr(binding, "LocalGitRefs", lambda *a, **kw: refs)
    monkeypatch.setattr(binding, "LocalFirstState", lambda db: state)
    monkeypatch.setattr(
        local_acceptance_refill, "select_next_after_acceptance",
        lambda **kw: SimpleNamespace(
            status="READY_FOR_LEASED_DISPATCH", reason="OK",
            next_slice_id="IG-001", accepted_sha=sha,
        ),
    )
    args["milestone_id"] = None
    args["task"] = None
    args["handoffs"] = {"IG-001": {
        "worktree_name": "ig001-worker-1", "branch": "iron-god/ig001",
        "objective": "Bounded implementation", "allowed_paths": [
            "src/agent.py", "tests/test_agent.py",
        ], "forbidden": ["secrets"], "acceptance": ["pytest passes"],
    }}
    received = []
    coordinator_module = ModuleType("accepted_event_dispatch_coordinator")
    coordinator_module.dispatch_one_accepted_event = (
        lambda **kw: received.append(kw)
    )
    monkeypatch.setitem(sys.modules, coordinator_module.__name__, coordinator_module)
    binding.dispatch_existing_accepted_event(**args)
    assert len(received) == 1
    dispatched = received[0]
    assert dispatched["state"] is state
    assert dispatched["refs"] is refs
    assert dispatched["milestone_id"] == "M-001"
    assert dispatched["task"]["id"] == "IG-001"
    assert dispatched["task"]["base_sha"] == sha
    assert dispatched["task"]["parent_id"] is None
    assert dispatched["task"]["repair"] is False
    assert dispatched["task"]["work"] == tmp_path / "ig001-worker-1"
    assert dispatched["task"]["prompt"] == (
        "Objective: Bounded implementation Allowed paths: src/agent.py, "
        "tests/test_agent.py. Forbidden: secrets. Acceptance: pytest passes"
    )
    assert not args["claims_path"].exists()


def test_implicit_milestone_ambiguity_blocks_without_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(authority, "owner_control_status", lambda root: authority.AUTHORIZED)
    args = arguments(tmp_path)
    LocalFirstState(args["state_db"])
    sha = "a" * 40
    refs = SimpleNamespace(accepted=lambda: sha, get=lambda kind, ident: sha)
    snapshot = {
        "acceptance_intents": [
            {"milestone_id": mid, "state": "COMMITTED", "accepted_sha": sha,
             "repository": "orchestrator", "verification_sha256": "b" * 64}
            for mid in ("M-001", "M-002")
        ],
        "milestones": [
            {"milestone_id": mid, "accepted_sha": sha,
             "repository": "orchestrator", "verification_sha256": "b" * 64}
            for mid in ("M-001", "M-002")
        ],
        "journal": [
            {"milestone_id": mid, "event_type": "MILESTONE_ACCEPTED", "detail": sha}
            for mid in ("M-001", "M-002")
        ],
    }
    monkeypatch.setattr(binding, "LocalGitRefs", lambda *a, **kw: refs)
    monkeypatch.setattr(
        binding, "LocalFirstState",
        lambda db: SimpleNamespace(snapshot=lambda: snapshot),
    )
    args["milestone_id"] = None
    args["task"] = None
    with pytest.raises(binding.ResidentDispatchBlocked, match="ACCEPTED_EVENT_NOT_UNIQUE"):
        binding.dispatch_existing_accepted_event(**args)
    assert not args["claims_path"].exists()


def test_implicit_task_selector_held_never_claims(tmp_path, monkeypatch):
    from project_orchestrator import local_acceptance_refill

    monkeypatch.setattr(authority, "owner_control_status", lambda root: authority.AUTHORIZED)
    args = arguments(tmp_path)
    LocalFirstState(args["state_db"])
    monkeypatch.setattr(
        binding, "LocalGitRefs",
        lambda *a, **kw: SimpleNamespace(accepted=lambda: "a" * 40),
    )
    monkeypatch.setattr(
        local_acceptance_refill, "select_next_after_acceptance",
        lambda **kw: SimpleNamespace(status="HELD", reason="NO_COMMITTED_EVENT"),
    )
    args["task"] = None
    with pytest.raises(
        binding.ResidentDispatchBlocked,
        match="DISPATCH_SELECTOR_NOT_READY:NO_COMMITTED_EVENT",
    ):
        binding.dispatch_existing_accepted_event(**args)
    assert not args["claims_path"].exists()


def test_derived_in_root_worktree_alias_never_claims(tmp_path, monkeypatch):
    """An in-root symlink alias must not disappear during task derivation."""
    from pathlib import Path
    from project_orchestrator import local_acceptance_refill

    monkeypatch.setattr(authority, "owner_control_status", lambda root: authority.AUTHORIZED)
    args = arguments(tmp_path)
    LocalFirstState(args["state_db"])
    sha = "a" * 40
    monkeypatch.setattr(
        binding, "LocalGitRefs",
        lambda *a, **kw: SimpleNamespace(accepted=lambda: sha),
    )
    monkeypatch.setattr(binding, "LocalFirstState", lambda db: object())
    monkeypatch.setattr(
        local_acceptance_refill, "select_next_after_acceptance",
        lambda **kw: SimpleNamespace(
            status="READY_FOR_LEASED_DISPATCH", reason="OK",
            next_slice_id="IG-001", accepted_sha=sha,
        ),
    )
    args["task"] = None
    args["handoffs"] = {"IG-001": {
        "worktree_name": "ig001-worker-1", "branch": "iron-god/ig001",
        "objective": "Bounded implementation",
        "allowed_paths": ["src/agent.py", "tests/test_agent.py"],
    }}
    alias = tmp_path / "ig001-worker-1"
    target = tmp_path / "another-worker"
    target.mkdir()
    original_resolve = Path.resolve
    original_is_symlink = Path.is_symlink

    def resolve_with_alias(path, *a, **kw):
        return target if path == alias else original_resolve(path, *a, **kw)

    def is_symlink_with_alias(path):
        return True if path == alias else original_is_symlink(path)

    monkeypatch.setattr(Path, "resolve", resolve_with_alias)
    monkeypatch.setattr(Path, "is_symlink", is_symlink_with_alias)
    coordinator_module = ModuleType("accepted_event_dispatch_coordinator")
    coordinator_module.dispatch_one_accepted_event = lambda **kw: pytest.fail(
        "in-root worktree alias must not reach the durable claim"
    )
    monkeypatch.setitem(sys.modules, coordinator_module.__name__, coordinator_module)
    with pytest.raises(
        binding.ResidentDispatchBlocked, match="DISPATCH_WORKTREE_NOT_READY"
    ):
        binding.dispatch_existing_accepted_event(**args)
    assert not args["claims_path"].exists()


def test_untrusted_controller_projection_path_blocks_before_state_open(tmp_path, monkeypatch):
    monkeypatch.setattr(authority, "owner_control_status", lambda root: authority.AUTHORIZED)
    args = arguments(tmp_path)
    args["expected_controller_state_path"] = tmp_path / "different-authority.json"
    with pytest.raises(binding.ResidentDispatchBlocked, match="PROTECTED_CONTROLLER_PATH_UNVERIFIED"):
        binding.dispatch_existing_accepted_event(**args)
    assert not args["state_db"].exists()
    assert not args["claims_path"].exists()


def test_protected_controller_revoked_before_state_open_blocks_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(authority, "owner_control_status", lambda root: authority.AUTHORIZED)
    args = arguments(tmp_path)
    changed = dict(args["controller"], candidate_execution_authorized=False)
    args["controller_state_path"].write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(binding.ResidentDispatchBlocked, match="PROTECTED_CONTROLLER_STATE_CHANGED"):
        binding.dispatch_existing_accepted_event(**args)
    assert not args["state_db"].exists()
    assert not args["claims_path"].exists()


def test_protected_controller_revoked_during_state_open_blocks_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(authority, "owner_control_status", lambda root: authority.AUTHORIZED)
    args = arguments(tmp_path)
    LocalFirstState(args["state_db"])
    monkeypatch.setattr(
        binding, "LocalGitRefs",
        lambda *a, **kw: SimpleNamespace(accepted=lambda: "a" * 40),
    )

    def state_constructor(db):
        changed = dict(args["controller"], mode="shadow_state_only")
        args["controller_state_path"].write_text(json.dumps(changed), encoding="utf-8")
        return object()

    monkeypatch.setattr(binding, "LocalFirstState", state_constructor)
    with pytest.raises(binding.ResidentDispatchBlocked, match="PROTECTED_CONTROLLER_STATE_CHANGED"):
        binding.dispatch_existing_accepted_event(**args)
    assert not args["claims_path"].exists()


def test_protected_controller_revoked_after_reservation_vetoes_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(authority, "owner_control_status", lambda root: authority.AUTHORIZED)
    args = arguments(tmp_path)
    LocalFirstState(args["state_db"])
    monkeypatch.setattr(
        binding, "LocalGitRefs",
        lambda *a, **kw: SimpleNamespace(accepted=lambda: "a" * 40),
    )
    args["worker_executor"] = lambda **kw: pytest.fail("revoked controller launched worker")
    captured = []
    coordinator_module = ModuleType("accepted_event_dispatch_coordinator")
    coordinator_module.dispatch_one_accepted_event = lambda **kw: captured.append(kw)
    monkeypatch.setitem(sys.modules, coordinator_module.__name__, coordinator_module)
    binding.dispatch_existing_accepted_event(**args)
    assert len(captured) == 1
    changed = dict(args["controller"], candidate_execution_authorized=False)
    args["controller_state_path"].write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(binding.ResidentDispatchBlocked, match="PROTECTED_CONTROLLER_STATE_CHANGED"):
        captured[0]["worker_executor"](object(), args["task"])
    assert not args["claims_path"].exists()
