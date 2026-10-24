"""Proves the full orchestration cycle (dispatch -> real isolated
build -> real verify -> Product Reality -> acceptance) correctly
BLOCKS when Product Reality is required and fails, per the owner's
2026-10-18 directive 2D: "Complete the remaining permitted
integration between the orchestrator, existing dispatch claims,
worker leases, verification, Product Reality, and acceptance
components. Use deterministic integration tests where real worker
execution is unavailable. Do not claim those tests prove a real
Claude subprocess has run."

This is deliberately generic (not Knowledge Engine Core-specific):
the real build_candidate/verify_and_commit substitutes are the same
lightweight-but-real fakes every proof this session uses (real file
writes, real git commits) -- the real claude subprocess builder
remains blocked (KE_CORE_REAL_BUILDER_VALIDATION_BLOCKED) and this
test does not claim otherwise. What is new here, not covered by any
existing test: run_continuous_development_cycle with
product_reality_required=True driven all the way through a FAILING
Product Reality result inside the full real dispatch/claim/lease
chain, not just the acceptance-gate function in isolation
(already covered by test_iron_god_candidate_local_acceptance.py and
test_iron_god_ke_core_product_reality_acceptance_integration.py).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

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
    assert CONTROLLER_PATH.read_bytes() == CONTROLLER_BACKUP_PATH.read_bytes()
    spec = importlib.util.spec_from_file_location(
        "orchestration_pr_blocking_proof_worker_module", CONTROLLER_PATH
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
    # Only the disposable proof fixture uses a stable resource supply.
    # The live worker still checks actual disk/RAM; the explicit veto
    # regression overrides this fixture to False to prove denial.
    monkeypatch.setattr(worker_module, "resources", lambda: True)
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
    env: dict, *, slice_id: str, worktree_name: str, branch: str
) -> dict:
    worker_module = env["worker_module"]
    packet = {
        "schema_version": 1,
        "slice_id": slice_id,
        "state": "ELIGIBLE",
        "worktree_name": worktree_name,
        "branch": branch,
        "exact_base_sha": env["base_sha"],
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
    (task["work"] / "src" / "proof.py").write_text("x = 1\n", encoding="utf-8")
    (task["work"] / "tests" / "test_proof.py").write_text(
        "def test_x():\n    assert True\n", encoding="utf-8"
    )


def _fake_verifier(task: dict, *, lease_heartbeat=None) -> dict:
    work = task["work"]
    _git(work, "add", "src/proof.py", "tests/test_proof.py")
    _git(work, "commit", "-q", "-m", "proof: " + task["id"])
    head = _git(work, "rev-parse", "HEAD")
    return {"head_sha": head, "receipt_sha256": "b" * 64, "checks": 0}


class _FailingAdapter:
    def evaluate(self, repository, candidate_sha):
        from project_orchestrator.iron_god_product_reality_adapter import (
            FAIL,
            EvidenceCategoryResult,
            ProductRealityEvidence,
        )

        return ProductRealityEvidence(
            repository=repository,
            candidate_sha=candidate_sha,
            category_results=(
                EvidenceCategoryResult("demo_category", FAIL, "deliberately fails"),
            ),
        )


def test_required_product_reality_failure_blocks_full_cycle(env, monkeypatch):
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

    _git(
        env["repo"],
        "worktree",
        "add",
        "-b",
        "iron-god/proof-slice-1",
        str(env["tmp_path"] / "wt-slice1"),
        env["base_sha"],
    )
    packet_1 = _write_handoff_packet(
        env,
        slice_id="PROOF-SLICE-1",
        worktree_name="wt-slice1",
        branch="iron-god/proof-slice-1",
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

    with pytest.raises(OrchestrationBlockedError, match="Product Reality blocked"):
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
            product_reality_adapter=_FailingAdapter(),
            product_reality_required=True,
            max_cycles=1,
        )

    # The real build genuinely ran and genuinely verified (a real commit
    # exists), but the accepted baseline must NOT have advanced -- Product
    # Reality is the determining factor for acceptance, not verification.
    assert env["refs"].accepted() == env["base_sha"]

    from project_orchestrator.local_dispatch_event_claim import inspect_claims

    claims = inspect_claims(env["tmp_path"] / "claims.sqlite")
    assert len(claims) == 1
    assert claims[0]["state"] == "RESULT_RECORDED"


def test_required_product_reality_success_allows_full_cycle_to_complete(
    env, monkeypatch
):
    worker_module = env["worker_module"]
    monkeypatch.setattr(worker_module, "build_candidate", _fake_builder)
    monkeypatch.setattr(worker_module, "verify_and_commit", _fake_verifier)

    from project_orchestrator.iron_god_orchestration_caller import (
        VERIFIED_CANDIDATE,
        run_continuous_development_cycle,
    )
    from project_orchestrator.resident_dispatch_binding import (
        dispatch_existing_accepted_event,
    )

    class _PassingAdapter:
        def evaluate(self, repository, candidate_sha):
            from project_orchestrator.iron_god_product_reality_adapter import (
                PASS,
                EvidenceCategoryResult,
                ProductRealityEvidence,
            )

            return ProductRealityEvidence(
                repository=repository,
                candidate_sha=candidate_sha,
                category_results=(
                    EvidenceCategoryResult("demo_category", PASS, "genuinely passes"),
                ),
            )

    _git(
        env["repo"],
        "worktree",
        "add",
        "-b",
        "iron-god/proof-slice-1",
        str(env["tmp_path"] / "wt-slice1"),
        env["base_sha"],
    )
    packet_1 = _write_handoff_packet(
        env,
        slice_id="PROOF-SLICE-1",
        worktree_name="wt-slice1",
        branch="iron-god/proof-slice-1",
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
        product_reality_adapter=_PassingAdapter(),
        product_reality_required=True,
        max_cycles=1,
    )

    assert len(outcomes) == 1
    assert outcomes[0].dispatch_status == VERIFIED_CANDIDATE
    assert outcomes[0].acceptance_status == "ACCEPTED"
    assert outcomes[0].product_reality_status == "PASS"
    assert env["refs"].accepted() == outcomes[0].candidate_sha
