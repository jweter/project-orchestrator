"""RDI-001 binding: no claim or worker until existing acceptance is verified."""

import sys
from types import ModuleType, SimpleNamespace

import pytest

from project_orchestrator import resident_dispatch_authority as authority
from project_orchestrator import resident_dispatch_binding as binding
from project_orchestrator.local_first_state import LocalFirstState


def arguments(tmp_path):
    return dict(
        controller={"mode": "candidate_mode", "candidate_execution_authorized": True},
        owner_state_root=tmp_path,
        expected_owner_state_root=tmp_path,
        state_db=tmp_path / "accepted.sqlite",
        repo=tmp_path,
        milestone_id="M-001",
        handoffs={"IG-001": {"state": "ELIGIBLE"}},
        claims_path=tmp_path / "claims.sqlite",
        worktree_root=tmp_path,
        task={"id": "IG-001"},
        worker_executor=lambda **kwargs: kwargs,
    )


def test_shadow_refuses_before_state_or_claim_creation(tmp_path, monkeypatch):
    monkeypatch.setattr(
        authority, "owner_control_status", lambda root: authority.AUTHORIZED
    )
    args = arguments(tmp_path)
    args["controller"]["mode"] = "shadow_state_only"
    args["controller"]["candidate_execution_authorized"] = False
    with pytest.raises(
        binding.ResidentDispatchBlocked, match="CANDIDATE_EXECUTION_NOT_AUTHORIZED"
    ):
        binding.dispatch_existing_accepted_event(**args)
    assert not args["state_db"].exists()
    assert not args["claims_path"].exists()


def test_missing_accepted_ref_refuses_without_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(
        authority, "owner_control_status", lambda root: authority.AUTHORIZED
    )
    args = arguments(tmp_path)
    LocalFirstState(args["state_db"])
    monkeypatch.setattr(
        binding,
        "LocalGitRefs",
        lambda repo, *, owner_state_root: SimpleNamespace(accepted=lambda: None),
    )
    monkeypatch.setattr(
        binding,
        "LocalFirstState",
        lambda db: pytest.fail("missing accepted ref must not open writable state"),
    )
    with pytest.raises(
        binding.ResidentDispatchBlocked, match="ACCEPTED_BASELINE_MISSING"
    ):
        binding.dispatch_existing_accepted_event(**args)
    assert not args["claims_path"].exists()


def test_reserved_worker_binds_exact_selector_state_and_refs(tmp_path, monkeypatch):
    monkeypatch.setattr(
        authority, "owner_control_status", lambda root: authority.AUTHORIZED
    )
    args = arguments(tmp_path)
    LocalFirstState(args["state_db"])
    refs = SimpleNamespace(accepted=lambda: "a" * 40)
    monkeypatch.setattr(binding, "LocalGitRefs", lambda repo, *, owner_state_root: refs)
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
        binding,
        "LocalGitRefs",
        lambda repo, *, owner_state_root: SimpleNamespace(accepted=lambda: "a" * 40),
    )
    with pytest.raises(binding.ResidentDispatchBlocked, match="OWNER_CONTROL_VETO"):
        binding.dispatch_existing_accepted_event(**args)
    assert not args["claims_path"].exists()


def test_invalid_milestone_fails_before_writable_state_open(tmp_path, monkeypatch):
    monkeypatch.setattr(
        authority, "owner_control_status", lambda root: authority.AUTHORIZED
    )
    args = arguments(tmp_path)
    LocalFirstState(args["state_db"])
    args["milestone_id"] = ""
    monkeypatch.setattr(
        binding,
        "LocalFirstState",
        lambda db: pytest.fail("state constructor must not run"),
    )
    with pytest.raises(
        binding.ResidentDispatchBlocked, match="ACCEPTED_MILESTONE_ID_MISSING"
    ):
        binding.dispatch_existing_accepted_event(**args)
    assert not args["claims_path"].exists()


def test_owner_takeover_before_reserved_worker_invocation_fails_closed(
    tmp_path, monkeypatch
):
    args = arguments(tmp_path)
    LocalFirstState(args["state_db"])
    owner = {"released": True}
    monkeypatch.setattr(
        authority,
        "owner_control_status",
        lambda root: authority.AUTHORIZED if owner["released"] else "BLOCKED",
    )
    monkeypatch.setattr(
        binding,
        "LocalGitRefs",
        lambda repo, *, owner_state_root: SimpleNamespace(accepted=lambda: "a" * 40),
    )
    args["worker_executor"] = lambda **kwargs: pytest.fail(
        "owner takeover must veto the reserved worker callback"
    )
    captured = []
    coordinator_module = ModuleType("accepted_event_dispatch_coordinator")
    coordinator_module.dispatch_one_accepted_event = lambda **kwargs: captured.append(
        kwargs
    )
    monkeypatch.setitem(sys.modules, coordinator_module.__name__, coordinator_module)
    binding.dispatch_existing_accepted_event(**args)
    assert len(captured) == 1
    owner["released"] = False
    with pytest.raises(binding.ResidentDispatchBlocked, match="OWNER_CONTROL_VETO"):
        captured[0]["worker_executor"](object(), args["task"])
    assert not args["claims_path"].exists()
