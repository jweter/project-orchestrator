"""Proves the orchestration caller drives two real, consecutive
development slices to completion automatically -- no manual
"continue" between them -- against a disposable real Git repository
and the real, unedited local_continuous_worker.py, per the owner's
2026-10-18 authorization.

Same isolation discipline as every other proof this session:
local_continuous_worker.py is loaded unedited via importlib and its
byte-identity against the pre-RDI-001 backup is asserted first; only
its module-level Path constants and the build_candidate/
verify_and_commit globals are substituted with lightweight-but-real
fakes (real file writes, real git commits). Every other real mechanism
(lease claim/heartbeat/transition, exact-head worktree validation,
dispatch claim fencing, invoke_once reservation, Product Reality gate,
real local acceptance via LocalFirstState+LocalGitRefs) runs
unmodified. No production file is read or written; no acceptance
record, mode, or candidate_execution_authorized flag in the real
IronGodLocal controller is touched.

The second slice's handoff packet cannot be pre-written with its real
exact_base_sha (slice 1's exact candidate commit is not knowable in
advance), so this test supplies a `handoff_planner_fn` standing in for
the real Capability Compiler (IG-060..067): once slice 1 is accepted,
it writes slice 2's handoff packet and ledger row with the now-known
accepted sha, exactly mirroring what a real planner would do.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

# This proof exercises an external, machine-installed IronGodLocal script.
# GitHub-hosted runners do not contain Azathoth's live controller sources.
_EXTERNAL_IRON_GOD_SCRIPT = Path(
    r"C:\Users\jerem\AppData\Local\ExponentialEngine\IronGodLocal\local_continuous_worker.py"
)
if not _EXTERNAL_IRON_GOD_SCRIPT.is_file():
    pytest.skip(
        "External IronGodLocal runtime source not installed on this runner",
        allow_module_level=True,
    )

CONTROLLER_PATH = Path(
    r"C:\Users\jerem\AppData\Local\ExponentialEngine\IronGodLocal\local_continuous_worker.py"
)
CONTROLLER_BACKUP_PATH = Path(
    r"C:\Users\jerem\AppData\Local\ExponentialEngine\IronGodLocal"
    r"\local_continuous_worker.py.post-pathfix-verified-20261018.py"
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
        "local_continuous_worker.py differs from its pre-RDI-001 backup -- "
        "refusing to run this proof against an unexpectedly modified live file"
    )
    spec = importlib.util.spec_from_file_location(
        "orchestration_caller_proof_worker_module", CONTROLLER_PATH
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

    assert worker_module.gate() is True

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


def _write_handoff_packet(
    env: dict, *, slice_id: str, worktree_name: str, branch: str, exact_base_sha: str
) -> dict:
    worker_module = env["worker_module"]
    packet = {
        "schema_version": 1,
        "slice_id": slice_id,
        "state": "ELIGIBLE",
        "worktree_name": worktree_name,
        "branch": branch,
        "exact_base_sha": exact_base_sha,
        "objective": "proof slice objective",
        "allowed_paths": ["src/proof.py", "tests/test_proof.py"],
        "forbidden": [],
        "acceptance": [],
        "repository": REPO_IDENTIFIER,
        "requires_milestones": [],
    }
    (worker_module.HANDOFF_DIR / (slice_id + ".json")).write_text(
        json.dumps(packet), encoding="utf-8"
    )
    return packet


def _fake_builder(task: dict, *, lease_heartbeat=None) -> None:
    (task["work"] / "src").mkdir(parents=True, exist_ok=True)
    (task["work"] / "tests").mkdir(parents=True, exist_ok=True)
    (task["work"] / "src" / "proof.py").write_text(
        "x = " + repr(task["id"]) + "\n", encoding="utf-8"
    )
    (task["work"] / "tests" / "test_proof.py").write_text(
        "def test_x():\n    assert True\n", encoding="utf-8"
    )


def _fake_verifier(task: dict, *, lease_heartbeat=None) -> dict:
    work = task["work"]
    _git(work, "add", "src/proof.py", "tests/test_proof.py")
    _git(work, "commit", "-q", "-m", "proof: " + task["id"])
    head = _git(work, "rev-parse", "HEAD")
    return {"head_sha": head, "receipt_sha256": "b" * 64, "checks": 0}


def _failing_verifier(task: dict, *, lease_heartbeat=None) -> dict:
    raise RuntimeError("EXACT_HEAD_NATIVE_PREFLIGHT_UNVERIFIED:" + task["id"])


def test_two_consecutive_slices_complete_automatically_without_manual_continuation(
    env, monkeypatch
):
    worker_module = env["worker_module"]
    monkeypatch.setattr(worker_module, "build_candidate", _fake_builder)
    monkeypatch.setattr(worker_module, "verify_and_commit", _fake_verifier)

    from project_orchestrator.iron_god_candidate_local_acceptance import ACCEPTED
    from project_orchestrator.iron_god_orchestration_caller import (
        VERIFIED_CANDIDATE,
        run_continuous_development_cycle,
    )
    from project_orchestrator.resident_dispatch_binding import (
        dispatch_existing_accepted_event,
    )

    packet_1 = _write_handoff_packet(
        env,
        slice_id="PROOF-SLICE-1",
        worktree_name="wt-slice1",
        branch="iron-god/proof-slice-1",
        exact_base_sha=env["base_sha"],
    )
    handoffs = {"PROOF-SLICE-1": packet_1}
    controller = {
        "controller": "iron-god-handoff-v1",
        "mode": "candidate_mode",
        "candidate_execution_authorized": True,
        "slices": [{"id": "PROOF-SLICE-1", "state": "ELIGIBLE"}],
    }

    def write_controller_state():
        worker_module.CONTROLLER_STATE.write_text(
            json.dumps(controller), encoding="utf-8"
        )

    write_controller_state()

    planned = {"slice_2_written": False}

    def handoff_planner_fn(
        handoffs_dict, controller_dict, repository, new_accepted_sha
    ):
        if planned["slice_2_written"]:
            return
        _write_handoff_packet(
            env,
            slice_id="PROOF-SLICE-2",
            worktree_name="wt-slice2",
            branch="iron-god/proof-slice-2",
            exact_base_sha=new_accepted_sha,
        )
        handoffs_dict["PROOF-SLICE-2"] = json.loads(
            (worker_module.HANDOFF_DIR / "PROOF-SLICE-2.json").read_text()
        )
        controller_dict["slices"].append({"id": "PROOF-SLICE-2", "state": "ELIGIBLE"})
        write_controller_state()
        planned["slice_2_written"] = True

    def worker_executor(claim, task, state, refs):
        return worker_module.execute_reserved_dispatch_claim(
            claim=claim,
            task=task,
            claims_path=env["tmp_path"] / "claims.sqlite",
            state=state,
            refs=refs,
            owner_state_root=env["flow"],
            lease_path=env["tmp_path"] / "leases.sqlite",
        )

    outcomes = run_continuous_development_cycle(
        orchestration_authorized=True,
        dispatch_fn=dispatch_existing_accepted_event,
        worker_executor=worker_executor,
        state=env["state"],
        state_db_path=env["tmp_path"] / "accepted.sqlite",
        refs=env["refs"],
        repo=env["repo"],
        repository=REPO_IDENTIFIER,
        authorized_repositories=frozenset({REPO_IDENTIFIER}),
        owner_state_root=env["flow"],
        controller=controller,
        handoffs=handoffs,
        claims_path=env["tmp_path"] / "claims.sqlite",
        lease_path=env["tmp_path"] / "leases.sqlite",
        worktree_root=env["tmp_path"],
        next_milestone_id_fn=lambda i: f"M-proof-{i + 1}",
        product_reality_adapter=None,
        product_reality_required=False,
        max_cycles=2,
        handoff_planner_fn=handoff_planner_fn,
    )

    assert len(outcomes) == 2
    first, second = outcomes
    assert first.slice_id == "PROOF-SLICE-1"
    assert first.dispatch_status == VERIFIED_CANDIDATE
    assert first.acceptance_status == ACCEPTED
    assert first.new_milestone_id == "M-proof-1"

    assert second.slice_id == "PROOF-SLICE-2"
    assert second.dispatch_status == VERIFIED_CANDIDATE
    assert second.acceptance_status == ACCEPTED
    assert second.new_milestone_id == "M-proof-2"
    assert second.candidate_sha != first.candidate_sha

    assert env["refs"].accepted() == second.candidate_sha
    assert env["refs"].get("milestone", "M-proof-2") == second.candidate_sha

    from project_orchestrator.local_dispatch_event_claim import inspect_claims

    claims = inspect_claims(env["tmp_path"] / "claims.sqlite")
    assert len(claims) == 2
    assert all(row["state"] == "RESULT_RECORDED" for row in claims)


def test_requires_its_own_explicit_authorization(env):
    from project_orchestrator.iron_god_orchestration_caller import (
        OrchestrationNotAuthorizedError,
        run_continuous_development_cycle,
    )
    from project_orchestrator.resident_dispatch_binding import (
        dispatch_existing_accepted_event,
    )

    with pytest.raises(OrchestrationNotAuthorizedError):
        run_continuous_development_cycle(
            orchestration_authorized=False,
            dispatch_fn=dispatch_existing_accepted_event,
            worker_executor=lambda **kwargs: {},
            state=env["state"],
            state_db_path=env["tmp_path"] / "accepted.sqlite",
            refs=env["refs"],
            repo=env["repo"],
            repository=REPO_IDENTIFIER,
            authorized_repositories=frozenset({REPO_IDENTIFIER}),
            owner_state_root=env["flow"],
            controller={"candidate_execution_authorized": True, "slices": []},
            handoffs={},
            claims_path=env["tmp_path"] / "claims.sqlite",
            lease_path=env["tmp_path"] / "leases.sqlite",
            worktree_root=env["tmp_path"],
            next_milestone_id_fn=lambda i: f"M-{i}",
            product_reality_adapter=None,
            product_reality_required=False,
            max_cycles=1,
        )


def test_stops_cleanly_with_no_dependency_ready_slice_and_no_planner(env, monkeypatch):
    worker_module = env["worker_module"]
    monkeypatch.setattr(worker_module, "build_candidate", _fake_builder)
    monkeypatch.setattr(worker_module, "verify_and_commit", _fake_verifier)

    from project_orchestrator.iron_god_orchestration_caller import (
        OrchestrationBlockedError,
        run_continuous_development_cycle,
    )
    from project_orchestrator.resident_dispatch_binding import (
        dispatch_existing_accepted_event,
    )

    packet_1 = _write_handoff_packet(
        env,
        slice_id="PROOF-SLICE-1",
        worktree_name="wt-slice1",
        branch="iron-god/proof-slice-1",
        exact_base_sha=env["base_sha"],
    )
    handoffs = {"PROOF-SLICE-1": packet_1}
    controller = {
        "controller": "iron-god-handoff-v1",
        "mode": "candidate_mode",
        "candidate_execution_authorized": True,
        "slices": [{"id": "PROOF-SLICE-1", "state": "ELIGIBLE"}],
    }
    worker_module.CONTROLLER_STATE.write_text(json.dumps(controller), encoding="utf-8")

    def worker_executor(claim, task, state, refs):
        return worker_module.execute_reserved_dispatch_claim(
            claim=claim,
            task=task,
            claims_path=env["tmp_path"] / "claims.sqlite",
            state=state,
            refs=refs,
            owner_state_root=env["flow"],
            lease_path=env["tmp_path"] / "leases.sqlite",
        )

    with pytest.raises(OrchestrationBlockedError, match="NO_DEPENDENCY_READY_SLICE"):
        run_continuous_development_cycle(
            orchestration_authorized=True,
            dispatch_fn=dispatch_existing_accepted_event,
            worker_executor=worker_executor,
            state=env["state"],
            state_db_path=env["tmp_path"] / "accepted.sqlite",
            refs=env["refs"],
            repo=env["repo"],
            repository=REPO_IDENTIFIER,
            authorized_repositories=frozenset({REPO_IDENTIFIER}),
            owner_state_root=env["flow"],
            controller=controller,
            handoffs=handoffs,
            claims_path=env["tmp_path"] / "claims.sqlite",
            lease_path=env["tmp_path"] / "leases.sqlite",
            worktree_root=env["tmp_path"],
            next_milestone_id_fn=lambda i: f"M-proof-{i + 1}",
            product_reality_adapter=None,
            product_reality_required=False,
            max_cycles=2,
            # No handoff_planner_fn -- slice 2 is never invented.
        )


def test_engineering_failure_stops_cycle_with_classified_reason_not_retried(
    env, monkeypatch
):
    worker_module = env["worker_module"]
    monkeypatch.setattr(worker_module, "build_candidate", _fake_builder)
    monkeypatch.setattr(worker_module, "verify_and_commit", _failing_verifier)

    from project_orchestrator.iron_god_orchestration_caller import (
        OrchestrationBlockedError,
        run_continuous_development_cycle,
    )
    from project_orchestrator.resident_dispatch_binding import (
        dispatch_existing_accepted_event,
    )

    packet_1 = _write_handoff_packet(
        env,
        slice_id="PROOF-SLICE-1",
        worktree_name="wt-slice1",
        branch="iron-god/proof-slice-1",
        exact_base_sha=env["base_sha"],
    )
    handoffs = {"PROOF-SLICE-1": packet_1}
    controller = {
        "controller": "iron-god-handoff-v1",
        "mode": "candidate_mode",
        "candidate_execution_authorized": True,
        "slices": [{"id": "PROOF-SLICE-1", "state": "ELIGIBLE"}],
    }
    worker_module.CONTROLLER_STATE.write_text(json.dumps(controller), encoding="utf-8")

    def worker_executor(claim, task, state, refs):
        return worker_module.execute_reserved_dispatch_claim(
            claim=claim,
            task=task,
            claims_path=env["tmp_path"] / "claims.sqlite",
            state=state,
            refs=refs,
            owner_state_root=env["flow"],
            lease_path=env["tmp_path"] / "leases.sqlite",
        )

    with pytest.raises(OrchestrationBlockedError) as exc_info:
        run_continuous_development_cycle(
            orchestration_authorized=True,
            dispatch_fn=dispatch_existing_accepted_event,
            worker_executor=worker_executor,
            state=env["state"],
            state_db_path=env["tmp_path"] / "accepted.sqlite",
            refs=env["refs"],
            repo=env["repo"],
            repository=REPO_IDENTIFIER,
            authorized_repositories=frozenset({REPO_IDENTIFIER}),
            owner_state_root=env["flow"],
            controller=controller,
            handoffs=handoffs,
            claims_path=env["tmp_path"] / "claims.sqlite",
            lease_path=env["tmp_path"] / "leases.sqlite",
            worktree_root=env["tmp_path"],
            next_milestone_id_fn=lambda i: f"M-proof-{i + 1}",
            product_reality_adapter=None,
            product_reality_required=False,
            max_cycles=2,
        )

    # The real controller already correctly decides, inside
    # execute_candidate_attempt, that a repairable verifier failure
    # (EXACT_HEAD_NATIVE_PREFLIGHT_UNVERIFIED) is a genuine engineering
    # failure, not a permission/owner-control denial -- it converts it
    # to a StageFailure and routes it through record_failure_and_queue_repair
    # internally, returning a normal {"status": "REPAIR_QUEUED", ...}
    # result rather than raising. So this never reaches this module's
    # own classify()-based exception branch (that branch exists for
    # signals the real controller raises bare, e.g. OWNER_OR_RESOURCE_VETO) --
    # the "not VERIFIED_CANDIDATE" branch is what stops the cycle here.
    outcomes = exc_info.value.outcomes
    assert len(outcomes) == 1
    assert outcomes[0].slice_id == "PROOF-SLICE-1"
    assert outcomes[0].dispatch_status == "REPAIR_QUEUED"
    assert "REPAIR_QUEUED" in outcomes[0].stopped_reason

    # The failing slice must have routed to the real repair queue, not
    # vanished or been silently retried -- confirmed the dispatch's own
    # claim still reached RESULT_RECORDED (repair routing is a successful
    # dispatch outcome at the claim layer, just not a VERIFIED_CANDIDATE).
    from project_orchestrator.local_dispatch_event_claim import inspect_claims

    claims = inspect_claims(env["tmp_path"] / "claims.sqlite")
    assert len(claims) == 1
    assert claims[0]["state"] == "RESULT_RECORDED"


# -- Repository-authorization boundary regression tests (owner directive,
# 2026-10-18, "OPTION 2 APPROVED: CLOSE THE AUTHORIZATION GAP AND PREPARE
# FOR LIVE EXECUTION"). These prove the boundary is enforced by this
# shared orchestration primitive itself -- the one real call path every
# existing caller uses -- not only by an entrypoint script's own
# discipline. ---------------------------------------------------------


def test_authorized_repository_task_passes_the_scope_check(env, monkeypatch):
    worker_module = env["worker_module"]
    monkeypatch.setattr(worker_module, "build_candidate", _fake_builder)
    monkeypatch.setattr(worker_module, "verify_and_commit", _fake_verifier)

    from project_orchestrator.iron_god_candidate_local_acceptance import ACCEPTED
    from project_orchestrator.iron_god_orchestration_caller import (
        VERIFIED_CANDIDATE,
        run_continuous_development_cycle,
    )
    from project_orchestrator.resident_dispatch_binding import (
        dispatch_existing_accepted_event,
    )

    packet = _write_handoff_packet(
        env,
        slice_id="PROOF-SLICE-SCOPE-OK",
        worktree_name="wt-scope-ok",
        branch="iron-god/proof-scope-ok",
        exact_base_sha=env["base_sha"],
    )
    handoffs = {"PROOF-SLICE-SCOPE-OK": packet}
    controller = {
        "controller": "iron-god-handoff-v1",
        "mode": "candidate_mode",
        "candidate_execution_authorized": True,
        "slices": [{"id": "PROOF-SLICE-SCOPE-OK", "state": "ELIGIBLE"}],
    }
    worker_module.CONTROLLER_STATE.write_text(json.dumps(controller), encoding="utf-8")

    def worker_executor(claim, task, state, refs):
        return worker_module.execute_reserved_dispatch_claim(
            claim=claim,
            task=task,
            claims_path=env["tmp_path"] / "claims.sqlite",
            state=state,
            refs=refs,
            owner_state_root=env["flow"],
            lease_path=env["tmp_path"] / "leases.sqlite",
        )

    outcomes = run_continuous_development_cycle(
        orchestration_authorized=True,
        dispatch_fn=dispatch_existing_accepted_event,
        worker_executor=worker_executor,
        state=env["state"],
        state_db_path=env["tmp_path"] / "accepted.sqlite",
        refs=env["refs"],
        repo=env["repo"],
        repository=REPO_IDENTIFIER,
        authorized_repositories=frozenset({REPO_IDENTIFIER}),
        owner_state_root=env["flow"],
        controller=controller,
        handoffs=handoffs,
        claims_path=env["tmp_path"] / "claims.sqlite",
        lease_path=env["tmp_path"] / "leases.sqlite",
        worktree_root=env["tmp_path"],
        next_milestone_id_fn=lambda i: f"M-proof-{i + 1}",
        product_reality_adapter=None,
        product_reality_required=False,
        max_cycles=1,
    )
    assert len(outcomes) == 1
    assert outcomes[0].dispatch_status == VERIFIED_CANDIDATE
    assert outcomes[0].acceptance_status == ACCEPTED


def test_cycle_repository_outside_authorized_set_is_rejected_before_any_dispatch(env):
    """An unauthorized target repository must never reach milestone
    resolution, selection, worktree provisioning, or the worker --
    the check is the very first thing this function does."""
    from project_orchestrator.iron_god_orchestration_caller import (
        OrchestrationNotAuthorizedError,
        run_continuous_development_cycle,
    )
    from project_orchestrator.resident_dispatch_binding import (
        dispatch_existing_accepted_event,
    )

    calls = []

    def worker_executor(claim, task, state, refs):
        calls.append((claim, task))
        raise AssertionError("worker_executor must never be invoked")

    with pytest.raises(
        OrchestrationNotAuthorizedError, match="not in the caller-declared"
    ):
        run_continuous_development_cycle(
            orchestration_authorized=True,
            dispatch_fn=dispatch_existing_accepted_event,
            worker_executor=worker_executor,
            state=env["state"],
            state_db_path=env["tmp_path"] / "accepted.sqlite",
            refs=env["refs"],
            repo=env["repo"],
            repository=REPO_IDENTIFIER,
            authorized_repositories=frozenset({"someone-else/other-repo"}),
            owner_state_root=env["flow"],
            controller={"candidate_execution_authorized": True, "slices": []},
            handoffs={},
            claims_path=env["tmp_path"] / "claims.sqlite",
            lease_path=env["tmp_path"] / "leases.sqlite",
            worktree_root=env["tmp_path"],
            next_milestone_id_fn=lambda i: f"M-{i}",
            product_reality_adapter=None,
            product_reality_required=False,
            max_cycles=1,
        )
    assert calls == []


def test_mismatched_accepted_baseline_in_a_handoff_packet_is_rejected(env):
    """A handoff packet whose exact_base_sha no longer matches the real
    accepted sha must never be selected for dispatch, even once the
    repository itself is correctly authorized."""
    from project_orchestrator.iron_god_orchestration_caller import (
        OrchestrationBlockedError,
        run_continuous_development_cycle,
    )
    from project_orchestrator.resident_dispatch_binding import (
        dispatch_existing_accepted_event,
    )

    packet = _write_handoff_packet(
        env,
        slice_id="PROOF-SLICE-STALE-BASELINE",
        worktree_name="wt-stale-baseline",
        branch="iron-god/proof-stale-baseline",
        exact_base_sha="f" * 40,  # does not match env["base_sha"]
    )
    handoffs = {"PROOF-SLICE-STALE-BASELINE": packet}
    controller = {
        "controller": "iron-god-handoff-v1",
        "mode": "candidate_mode",
        "candidate_execution_authorized": True,
        "slices": [{"id": "PROOF-SLICE-STALE-BASELINE", "state": "ELIGIBLE"}],
    }
    env["worker_module"].CONTROLLER_STATE.write_text(
        json.dumps(controller), encoding="utf-8"
    )

    calls = []

    def worker_executor(claim, task, state, refs):
        calls.append((claim, task))
        raise AssertionError("worker_executor must never be invoked")

    with pytest.raises(OrchestrationBlockedError, match="NO_DEPENDENCY_READY_SLICE"):
        run_continuous_development_cycle(
            orchestration_authorized=True,
            dispatch_fn=dispatch_existing_accepted_event,
            worker_executor=worker_executor,
            state=env["state"],
            state_db_path=env["tmp_path"] / "accepted.sqlite",
            refs=env["refs"],
            repo=env["repo"],
            repository=REPO_IDENTIFIER,
            authorized_repositories=frozenset({REPO_IDENTIFIER}),
            owner_state_root=env["flow"],
            controller=controller,
            handoffs=handoffs,
            claims_path=env["tmp_path"] / "claims.sqlite",
            lease_path=env["tmp_path"] / "leases.sqlite",
            worktree_root=env["tmp_path"],
            next_milestone_id_fn=lambda i: f"M-proof-{i + 1}",
            product_reality_adapter=None,
            product_reality_required=False,
            max_cycles=1,
        )
    assert calls == []


def test_owner_takeover_still_blocks_execution_even_when_repository_is_authorized(
    env,
):
    """Flipping owner control ON (human-mode.json enabled=true) must still
    stop dispatch -- the new repository boundary is additive, it never
    substitutes for or weakens the existing owner-control veto."""
    import json as _json

    from project_orchestrator.iron_god_orchestration_caller import (
        OrchestrationBlockedError,
        run_continuous_development_cycle,
    )
    from project_orchestrator.resident_dispatch_binding import (
        dispatch_existing_accepted_event,
    )

    packet = _write_handoff_packet(
        env,
        slice_id="PROOF-SLICE-OWNER-VETO",
        worktree_name="wt-owner-veto",
        branch="iron-god/proof-owner-veto",
        exact_base_sha=env["base_sha"],
    )
    handoffs = {"PROOF-SLICE-OWNER-VETO": packet}
    controller = {
        "controller": "iron-god-handoff-v1",
        "mode": "candidate_mode",
        "candidate_execution_authorized": True,
        "slices": [{"id": "PROOF-SLICE-OWNER-VETO", "state": "ELIGIBLE"}],
    }
    env["worker_module"].CONTROLLER_STATE.write_text(
        _json.dumps(controller), encoding="utf-8"
    )

    # The real owner-takeover switch: flip it ON after the fixture set it
    # off, exactly as a human reclaiming control would.
    (env["flow"] / "human-mode.json").write_text(
        _json.dumps({"enabled": True}), encoding="utf-8"
    )

    calls = []

    def worker_executor(claim, task, state, refs):
        calls.append((claim, task))
        raise AssertionError("worker_executor must never be invoked")

    with pytest.raises(OrchestrationBlockedError):
        run_continuous_development_cycle(
            orchestration_authorized=True,
            dispatch_fn=dispatch_existing_accepted_event,
            worker_executor=worker_executor,
            state=env["state"],
            state_db_path=env["tmp_path"] / "accepted.sqlite",
            refs=env["refs"],
            repo=env["repo"],
            repository=REPO_IDENTIFIER,
            authorized_repositories=frozenset({REPO_IDENTIFIER}),
            owner_state_root=env["flow"],
            controller=controller,
            handoffs=handoffs,
            claims_path=env["tmp_path"] / "claims.sqlite",
            lease_path=env["tmp_path"] / "leases.sqlite",
            worktree_root=env["tmp_path"],
            next_milestone_id_fn=lambda i: f"M-proof-{i + 1}",
            product_reality_adapter=None,
            product_reality_required=False,
            max_cycles=1,
        )
    assert calls == []


def test_direct_coordinator_call_has_no_repository_scope_concept_at_all(
    env, monkeypatch
):
    """Honest boundary documentation, not a desired property: calling
    dispatch_existing_accepted_event directly -- skipping
    run_continuous_development_cycle entirely -- dispatches a real task
    with zero regard for any "authorized repository" notion, because
    neither this function nor dispatch_one_accepted_event beneath it
    accept or check an authorized_repositories parameter at all (asserted
    below against the real signature, not assumed). Repository scoping is
    not a structural property of this layer; it is added one layer up, by
    run_continuous_development_cycle, which is the only actual caller of
    this layer anywhere in this codebase today (confirmed by direct
    search). A new caller that skipped the orchestration layer would
    skip the scoping too -- this test proves that fact rather than
    hiding it, per the owner's explicit instruction to verify this
    rather than claim the system is fully scoped merely because its
    preferred entry point is scoped."""
    import inspect

    worker_module = env["worker_module"]
    monkeypatch.setattr(worker_module, "build_candidate", _fake_builder)
    monkeypatch.setattr(worker_module, "verify_and_commit", _fake_verifier)

    from project_orchestrator.resident_dispatch_binding import (
        dispatch_existing_accepted_event,
    )

    assert (
        "authorized_repositories"
        not in inspect.signature(dispatch_existing_accepted_event).parameters
    )

    packet = _write_handoff_packet(
        env,
        slice_id="PROOF-SLICE-DIRECT-CALL",
        worktree_name="wt-direct-call",
        branch="iron-god/proof-direct-call",
        exact_base_sha=env["base_sha"],
    )
    handoffs = {"PROOF-SLICE-DIRECT-CALL": packet}
    controller = {
        "controller": "iron-god-handoff-v1",
        "mode": "candidate_mode",
        "candidate_execution_authorized": True,
        "slices": [{"id": "PROOF-SLICE-DIRECT-CALL", "state": "ELIGIBLE"}],
    }
    worker_module.CONTROLLER_STATE.write_text(json.dumps(controller), encoding="utf-8")

    calls = []

    def worker_executor(claim, task, state, refs):
        calls.append((claim, task))
        return worker_module.execute_reserved_dispatch_claim(
            claim=claim,
            task=task,
            claims_path=env["tmp_path"] / "claims.sqlite",
            state=state,
            refs=refs,
            owner_state_root=env["flow"],
            lease_path=env["tmp_path"] / "leases.sqlite",
        )

    from accepted_event_dispatch_coordinator import build_expected_task

    from project_orchestrator.iron_god_orchestration_caller import (
        provision_candidate_worktree,
    )

    task = build_expected_task(
        slice_id="PROOF-SLICE-DIRECT-CALL",
        accepted_sha=env["base_sha"],
        packet=packet,
        worktree_root=env["tmp_path"],
    )
    provision_candidate_worktree(
        env["repo"],
        worktree_root=env["tmp_path"],
        worktree_name=packet["worktree_name"],
        branch=packet["branch"],
        base_sha=packet["exact_base_sha"],
    )

    result = dispatch_existing_accepted_event(
        controller=controller,
        owner_state_root=env["flow"],
        expected_owner_state_root=env["flow"],
        state_db=env["tmp_path"] / "accepted.sqlite",
        repo=env["repo"],
        milestone_id="M-proof-0",
        handoffs=handoffs,
        claims_path=env["tmp_path"] / "claims.sqlite",
        worktree_root=env["tmp_path"],
        task=task,
        worker_executor=worker_executor,
    )

    # Dispatch simply succeeded -- no authorized_repositories check exists
    # at this layer to have blocked anything. Proves the boundary is NOT
    # a structural property of this function; it is added one layer up.
    assert len(calls) == 1
    assert result["status"] in ("VERIFIED_CANDIDATE", "REPAIR_QUEUED")
