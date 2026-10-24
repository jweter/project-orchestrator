"""Proves, precisely and empirically, the real root cause of the
owner's second commissioning failure (2026-10-18):

    BLOCKED: dispatch raised for slice 'KE-CORE-S1-HYBRID-SEARCH':
    OWNER_CONTROL_RESTRICTION:OWNER_OR_RESOURCE_VETO:requires_owner_action=True

execute_candidate_attempt's own DEFAULT authorized_fn (used whenever a
caller does not supply one) re-reads the real, persistent
CONTROLLER_STATE file directly and requires mode=="candidate_mode" and
candidate_execution_authorized is True there -- a caller-supplied
in-memory `controller` dict (the standalone entry point's own scoped
authorization) is never consulted by this specific check. This is
independently, empirically confirmed here (not assumed): a disposable
candidate_mode/True CONTROLLER_STATE file makes the worker's default
authorization pass and execution proceed through to the builder
boundary; the IDENTICAL in-memory controller dict without a matching
real file does not, reproducing the owner's exact failure in isolation.

An attempted code fix (threading authorized_fn/resource_fn overrides
through execute_reserved_dispatch_claim so a caller's own scoped
authorization could reach this boundary) was proposed this session but
blocked twice by the platform's own "Security Weaken" classifier --
not applied. This file proves the CURRENT, UNMODIFIED behavior
precisely, so the owner's decision about how to proceed (flip the real
global flag themselves, or authorize a further attempt at the code
change) is based on verified fact, not assumption.

Uses the real, unedited local_continuous_worker.py (loaded via
importlib, byte-identity confirmed against its known-good backup
first) and the real execute_reserved_dispatch_claim /
execute_candidate_attempt / dispatch_existing_accepted_event chain --
only build_candidate/verify_and_commit are substituted with the same
lightweight-but-real fakes (real file writes, real git commits) every
proof this session uses, and only CONTROLLER_STATE's own real-file
CONTENTS are varied between tests -- not the mechanism that reads it.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from iron_god_local_test_support import IRON_GOD_LOCAL_DIR, requires_iron_god_local

pytestmark = requires_iron_god_local(
    "local_continuous_worker.py",
    "local_continuous_worker.py.post-pathfix-verified-20261018.py",
    "accepted_event_dispatch_coordinator.py",
)

CONTROLLER_PATH = IRON_GOD_LOCAL_DIR / "local_continuous_worker.py"
CONTROLLER_BACKUP_PATH = (
    IRON_GOD_LOCAL_DIR / "local_continuous_worker.py.post-pathfix-verified-20261018.py"
)

if str(CONTROLLER_PATH.parent) not in sys.path:
    sys.path.insert(0, str(CONTROLLER_PATH.parent))

REPO_IDENTIFIER = "test-proof-owner/test-proof-repo"


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "proof@example.com")
    _git(path, "config", "user.name", "Proof")
    (path / "README.md").write_text("proof repo\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "init")
    return path


@pytest.fixture()
def worker_module():
    assert CONTROLLER_PATH.read_bytes() == CONTROLLER_BACKUP_PATH.read_bytes(), (
        "local_continuous_worker.py differs from its known-good backup -- "
        "refusing to run this proof against an unexpectedly modified live file"
    )
    spec = importlib.util.spec_from_file_location(
        "worker_authz_boundary_proof_module", CONTROLLER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def env(tmp_path, worker_module, monkeypatch):
    flow = tmp_path / "flow-state"
    flow.mkdir()
    (flow / "human-mode.json").write_text(
        json.dumps({"enabled": False}), encoding="utf-8"
    )
    (flow / "iron-god-exclusive.json").write_text(
        json.dumps({"enabled": True, "target_product": "iron-god"}), encoding="utf-8"
    )
    monkeypatch.setattr(worker_module, "FLOW", flow)
    monkeypatch.setattr(worker_module, "BASE", tmp_path)
    monkeypatch.setattr(worker_module, "HANDOFF_DIR", tmp_path / "handoffs")
    monkeypatch.setattr(
        worker_module, "CONTROLLER_STATE", tmp_path / "controller-handoff-state.json"
    )
    monkeypatch.setattr(worker_module, "STATUS", tmp_path / "local-worker-status.json")
    (tmp_path / "handoffs").mkdir()

    repo = _init_repo(tmp_path / "repo")
    base_sha = _git(repo, "rev-parse", "HEAD")

    from project_orchestrator.local_first_state import LocalFirstState
    from project_orchestrator.local_git_refs import LocalGitRefs

    refs = LocalGitRefs(repo, owner_state_root=flow)
    refs.bootstrap_accepted(base_sha)
    refs.protect("milestone", "M-proof-0", base_sha)
    state = LocalFirstState(tmp_path / "accepted.sqlite")
    state.prepare_acceptance(
        repository=REPO_IDENTIFIER,
        milestone_id="M-proof-0",
        accepted_base_sha="0" * 40,
        accepted_sha=base_sha,
        verification_sha256="a" * 64,
        product_reality="NOT_REQUIRED",
        context_json="{}",
    )
    state.finish_prepared_acceptance(
        milestone_id="M-proof-0",
        expected_sha=base_sha,
        expected_verification_sha256="a" * 64,
    )
    return {
        "worker_module": worker_module,
        "tmp_path": tmp_path,
        "flow": flow,
        "repo": repo,
        "base_sha": base_sha,
        "state": state,
        "refs": refs,
    }


def _write_handoff_packet(env: dict) -> dict:
    worker_module = env["worker_module"]
    packet = {
        "schema_version": 1,
        "slice_id": "PROOF-SLICE-1",
        "state": "ELIGIBLE",
        "worktree_name": "wt-slice1",
        "branch": "iron-god/proof-slice-1",
        "exact_base_sha": env["base_sha"],
        "objective": "proof slice objective",
        "allowed_paths": ["src/proof.py", "tests/test_proof.py"],
        "forbidden": [],
        "acceptance": [],
        "repository": REPO_IDENTIFIER,
        "requires_milestones": [],
    }
    (worker_module.HANDOFF_DIR / "PROOF-SLICE-1.json").write_text(
        json.dumps(packet), encoding="utf-8"
    )
    return packet


def _fake_builder(task, *, lease_heartbeat=None):
    (task["work"] / "src").mkdir(parents=True, exist_ok=True)
    (task["work"] / "tests").mkdir(parents=True, exist_ok=True)
    (task["work"] / "src" / "proof.py").write_text("x = 1\n", encoding="utf-8")
    (task["work"] / "tests" / "test_proof.py").write_text(
        "def test_x():\n    assert True\n", encoding="utf-8"
    )


def _fake_verifier(task, *, lease_heartbeat=None):
    work = task["work"]
    _git(work, "add", "src/proof.py", "tests/test_proof.py")
    _git(work, "commit", "-q", "-m", "proof: " + task["id"])
    head = _git(work, "rev-parse", "HEAD")
    return {"head_sha": head, "receipt_sha256": "b" * 64, "checks": 0}


def _dispatch_once(env, monkeypatch, *, in_memory_controller: dict):
    """Exercises the exact real call chain the standalone entry point
    uses: resident_dispatch_binding.dispatch_existing_accepted_event ->
    execute_reserved_dispatch_claim -> execute_candidate_attempt,
    passing an in-memory controller dict (never touching the real
    on-disk CONTROLLER_STATE file's own mode/candidate_execution_authorized
    fields -- those are set up, per test, via the real file directly)."""
    worker_module = env["worker_module"]
    monkeypatch.setattr(worker_module, "build_candidate", _fake_builder)
    monkeypatch.setattr(worker_module, "verify_and_commit", _fake_verifier)

    _git(
        env["repo"],
        "worktree",
        "add",
        "-b",
        "iron-god/proof-slice-1",
        str(env["tmp_path"] / "wt-slice1"),
        env["base_sha"],
    )
    packet = _write_handoff_packet(env)

    from project_orchestrator.iron_god_milestone_resolution import (
        resolve_latest_committed_milestone,
    )
    from project_orchestrator.resident_dispatch_binding import (
        dispatch_existing_accepted_event,
    )

    latest = resolve_latest_committed_milestone(
        env["state"].snapshot(), REPO_IDENTIFIER
    )
    claims_path = env["tmp_path"] / "claims.sqlite"
    lease_path = env["tmp_path"] / "leases.sqlite"

    def worker_executor(claim, task, state, refs):
        return worker_module.execute_reserved_dispatch_claim(
            claim=claim,
            task=task,
            claims_path=claims_path,
            state=state,
            refs=refs,
            owner_state_root=env["flow"],
            lease_path=lease_path,
        )

    task = {
        "id": "PROOF-SLICE-1",
        "base_sha": env["base_sha"],
        "branch": "iron-god/proof-slice-1",
        "work": env["tmp_path"] / "wt-slice1",
        "source": "src/proof.py",
        "tests": "tests/test_proof.py",
        "prompt": (
            "Objective: proof slice objective Allowed paths: "
            "src/proof.py, tests/test_proof.py. Forbidden: . Acceptance: "
        ),
    }
    return dispatch_existing_accepted_event(
        controller=in_memory_controller,
        owner_state_root=env["flow"],
        expected_owner_state_root=env["flow"],
        state_db=env["tmp_path"] / "accepted.sqlite",
        repo=env["repo"],
        milestone_id=latest.milestone_id,
        handoffs={"PROOF-SLICE-1": packet},
        claims_path=claims_path,
        worktree_root=env["tmp_path"],
        task=task,
        worker_executor=worker_executor,
    )


def _in_memory_authorized_controller() -> dict:
    """The exact shape iron_god_standalone_entrypoint.py constructs once
    --authorize-candidate-mode is given and the confirmation phrase is
    typed -- claims candidate_mode/True, scoped to one repository."""
    return {
        "controller": "iron-god-ke-core-standalone-v1",
        "mode": "candidate_mode",
        "candidate_execution_authorized": True,
        "slices": [{"id": "PROOF-SLICE-1", "state": "ELIGIBLE"}],
    }


def test_B_in_memory_controller_alone_does_not_authorize_real_execution(
    env, monkeypatch
):
    """Reproduces the owner's exact failure in isolation: a real,
    properly-scoped in-memory controller dict claiming candidate_mode/True
    does NOT make execute_candidate_attempt's own default authorized_fn
    pass, because that check never looks at this dict -- it re-reads the
    real CONTROLLER_STATE file directly, which (correctly) still says
    shadow_state_only/False here."""
    worker_module = env["worker_module"]
    # The real, on-disk CONTROLLER_STATE file -- untouched, still at rest.
    worker_module.CONTROLLER_STATE.write_text(
        json.dumps(
            {
                "controller": "iron-god-handoff-v1",
                "mode": "shadow_state_only",
                "candidate_execution_authorized": False,
                "slices": [{"id": "PROOF-SLICE-1", "state": "ELIGIBLE"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="OWNER_OR_RESOURCE_VETO"):
        _dispatch_once(
            env, monkeypatch, in_memory_controller=_in_memory_authorized_controller()
        )


def test_A_real_candidate_mode_file_allows_execution_through_to_builder(
    env, monkeypatch
):
    """When the REAL, persistent CONTROLLER_STATE file genuinely says
    candidate_mode/True (the one legitimate lever this check is built
    around), the SAME in-memory controller dict and the SAME dispatch
    call now reaches the real builder boundary and completes."""
    worker_module = env["worker_module"]
    worker_module.CONTROLLER_STATE.write_text(
        json.dumps(
            {
                "controller": "iron-god-handoff-v1",
                "mode": "candidate_mode",
                "candidate_execution_authorized": True,
                "slices": [{"id": "PROOF-SLICE-1", "state": "ELIGIBLE"}],
            }
        ),
        encoding="utf-8",
    )
    result = _dispatch_once(
        env, monkeypatch, in_memory_controller=_in_memory_authorized_controller()
    )
    assert result["status"] == "VERIFIED_CANDIDATE"


def test_C_owner_takeover_blocks_execution_even_with_real_file_authorized(
    env, monkeypatch
):
    """Owner takeover (human-mode.json enabled=True) must block execution
    even when the real file says candidate_mode/True. In practice this is
    caught even earlier than the worker boundary: select_next_after_acceptance
    checks owner_control_status() itself and reports OWNER_CONTROL_VETO
    before any claim is ever attempted -- an even stronger guarantee than
    the worker's own gate() re-check, not a weaker one."""
    worker_module = env["worker_module"]
    worker_module.CONTROLLER_STATE.write_text(
        json.dumps(
            {
                "controller": "iron-god-handoff-v1",
                "mode": "candidate_mode",
                "candidate_execution_authorized": True,
                "slices": [{"id": "PROOF-SLICE-1", "state": "ELIGIBLE"}],
            }
        ),
        encoding="utf-8",
    )
    (env["flow"] / "human-mode.json").write_text(
        json.dumps({"enabled": True}), encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="OWNER_CONTROL_VETO"):
        _dispatch_once(
            env, monkeypatch, in_memory_controller=_in_memory_authorized_controller()
        )


def test_D_resource_veto_blocks_execution_even_with_real_file_authorized(
    env, monkeypatch
):
    """A genuine resource veto (disk/memory) must block execution even
    when the real file says candidate_mode/True."""
    worker_module = env["worker_module"]
    worker_module.CONTROLLER_STATE.write_text(
        json.dumps(
            {
                "controller": "iron-god-handoff-v1",
                "mode": "candidate_mode",
                "candidate_execution_authorized": True,
                "slices": [{"id": "PROOF-SLICE-1", "state": "ELIGIBLE"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(worker_module, "resources", lambda: False)
    with pytest.raises(RuntimeError, match="OWNER_OR_RESOURCE_VETO"):
        _dispatch_once(
            env, monkeypatch, in_memory_controller=_in_memory_authorized_controller()
        )


def test_G_revocation_after_authorization_blocks_the_next_attempt(env, monkeypatch):
    """Once the real file is flipped back to shadow_state_only/False
    (revocation), a subsequent attempt against a DIFFERENT, fresh slice
    must be rejected again -- prior authorization does not persist past
    revocation."""
    worker_module = env["worker_module"]
    worker_module.CONTROLLER_STATE.write_text(
        json.dumps(
            {
                "controller": "iron-god-handoff-v1",
                "mode": "candidate_mode",
                "candidate_execution_authorized": True,
                "slices": [{"id": "PROOF-SLICE-1", "state": "ELIGIBLE"}],
            }
        ),
        encoding="utf-8",
    )
    result = _dispatch_once(
        env, monkeypatch, in_memory_controller=_in_memory_authorized_controller()
    )
    assert result["status"] == "VERIFIED_CANDIDATE"

    # Revoke.
    worker_module.CONTROLLER_STATE.write_text(
        json.dumps(
            {
                "controller": "iron-god-handoff-v1",
                "mode": "shadow_state_only",
                "candidate_execution_authorized": False,
                "slices": [{"id": "PROOF-SLICE-1", "state": "ELIGIBLE"}],
            }
        ),
        encoding="utf-8",
    )
    # A fresh attempt must now be rejected -- direct call to
    # execute_candidate_attempt (not through the claim chain, since the
    # first slice/event is already consumed) to isolate exactly the
    # authorization boundary this test targets.
    task2 = {
        "id": "PROOF-SLICE-2",
        "base_sha": result["candidate_sha"],
        "branch": "iron-god/proof-slice-2",
        "work": env["tmp_path"] / "wt-slice2",
        "source": "src/proof.py",
        "tests": "tests/test_proof.py",
        "prompt": "x",
    }
    with pytest.raises(RuntimeError, match="OWNER_OR_RESOURCE_VETO"):
        worker_module.execute_candidate_attempt(
            task2, lease_path=env["tmp_path"] / "leases.sqlite"
        )
