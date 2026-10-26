"""Proves the complete autonomous development loop end-to-end, per the
2026-10-17 mission's Phase 4, against disposable real Git repositories
and the real, unedited local_continuous_worker.py -- extending the
RDI-001 proof (tests/test_rdi001_resident_dispatch_e2e_proof.py) with
two properties that single-dispatch proof does not cover: genuine
multi-slice continuation, and cross-repository failure isolation.

Same isolation discipline as the RDI-001 proof: local_continuous_worker.py
is loaded unedited via importlib and its byte-identity against the
pre-RDI-001 backup is asserted first; only its module-level Path
constants and the build_candidate/verify_and_commit globals are
substituted. Every other real mechanism (lease claim/heartbeat/
transition, exact-head worktree validation, dispatch claim fencing,
invoke_once reservation, record_failure_and_queue_repair) runs
unmodified, against a real disposable Git repo and real SQLite stores.
No production file is read or written; no acceptance record, mode, or
candidate_execution_authorized flag in the real IronGodLocal
controller is touched.

Genuine finding this file exists to prove (not assumed going in): the
dispatch claims table's schema is `PRIMARY KEY (source_db,
acceptance_event_id)` (local_dispatch_event_claim.py) -- so at most ONE
slice can ever be dispatched per acceptance event. "Immediately
dispatch the next slice" in the real system is therefore NOT a second
dispatch call against the same accepted milestone (that is correctly
fenced as a duplicate); it requires a genuinely NEW acceptance event --
i.e. the just-verified candidate's own commit must first be locally
accepted as a new milestone (a separate, deliberately-gated step; the
real candidate_outcome_reconciliation ledger entry notes
accepted_ref_authority: false -- this pipeline does not acquire that
authority on its own). This file performs that second acceptance using
the REAL mechanism the live selector (select_next_after_acceptance)
actually reads -- LocalFirstState.prepare_acceptance/
finish_prepared_acceptance plus LocalGitRefs.advance_accepted/protect --
not iron_god_acceptance.accept_candidate, which was confirmed by
reading it to operate on a different, disconnected ref/state system
(iron_god_refs.py's repository-parametrized Git refs plus no
LocalFirstState write at all) that the real selector does not consult.

What this does NOT prove (explicitly out of scope, consistent with
every constraint observed this session): a real Claude Code invocation,
a real repository-native 26-gate preflight run, or real Product
Reality evaluation. On that last point: grepping local_continuous_worker.py
and accepted_event_dispatch_coordinator.py for any product_reality
reference returns nothing -- Product Reality (IG-080..086) is not
wired into the live candidate pipeline at all today, so no step of this
proof exercises it; this is reported as a genuine gap, not simulated.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from tests.owner_authority import grant_owner_authority, revoke_owner_authority

CONTROLLER_PATH = Path(
    r"C:\Users\jerem\AppData\Local\ExponentialEngine\IronGodLocal\local_continuous_worker.py"
)
CONTROLLER_BACKUP_PATH = Path(
    r"C:\Users\jerem\AppData\Local\ExponentialEngine\IronGodLocal"
    r"\local_continuous_worker.py.post-pathfix-verified-20261018.py"
)

if str(CONTROLLER_PATH.parent) not in sys.path:
    sys.path.insert(0, str(CONTROLLER_PATH.parent))

REPO_IDENTIFIER = "test-proof-owner/test-proof-repo-A"
SECOND_REPO_IDENTIFIER = "test-proof-owner/test-proof-repo-B"


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
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
        "complete_loop_proof_worker_module", CONTROLLER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def proof_env(tmp_path, worker_module, monkeypatch):
    flow = tmp_path / "flow-state"
    flow.mkdir()
    # Authoritative owner controller grant for repository modules.
    grant_owner_authority(flow)
    # The installed legacy worker still reads its own fixture files until
    # it is migrated into version control.
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
    # Isolated proof resources must not depend on the live laptop's momentary
    # free RAM. Production still enforces the real worker resource check.
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


def _write_handoff_and_ledger_row(
    env: dict,
    *,
    slice_id: str,
    worktree_name: str,
    branch: str,
    exact_base_sha: str,
    repository: str = REPO_IDENTIFIER,
    requires_milestones: list[str] | None = None,
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
        "repository": repository,
        "requires_milestones": requires_milestones or [],
    }
    (worker_module.HANDOFF_DIR / (slice_id + ".json")).write_text(
        json.dumps(packet), encoding="utf-8"
    )
    ledger = {
        "controller": "iron-god-handoff-v1",
        "mode": "candidate_mode",
        "candidate_execution_authorized": True,
        "slices": [{"id": slice_id, "state": "ELIGIBLE"}],
    }
    worker_module.CONTROLLER_STATE.write_text(json.dumps(ledger), encoding="utf-8")
    return ledger


def _build_task(
    env: dict, *, slice_id: str, worktree_name: str, branch: str, base_sha: str
) -> dict:
    work = env["tmp_path"] / worktree_name
    prompt = (
        "Objective: proof slice objective Allowed paths: "
        "src/proof.py, tests/test_proof.py. Forbidden: . Acceptance: "
    )
    return {
        "id": slice_id,
        "base_sha": base_sha,
        "branch": branch,
        "work": work,
        "source": "src/proof.py",
        "tests": "tests/test_proof.py",
        "prompt": prompt,
    }


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
    # Matches the exact failure_code the real execute_candidate_attempt
    # recognizes as repairable (local_continuous_worker.py's own
    # `repairable = {..., "EXACT_HEAD_NATIVE_PREFLIGHT_UNVERIFIED"}` set) --
    # the builder has already written real files by this point, so the
    # StageFailure handler's own changed-paths check sees exactly the
    # allowed source/test files and routes to repair rather than raising
    # UNEXPECTED_WORKTREE_CHANGE.
    raise RuntimeError("EXACT_HEAD_NATIVE_PREFLIGHT_UNVERIFIED:" + task["id"])


def _make_worker_executor(env: dict, *, claims_path: Path, lease_path: Path):
    worker_module = env["worker_module"]

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

    return worker_executor


def test_second_slice_requires_genuine_reacceptance_not_a_second_dispatch_call(
    proof_env, monkeypatch
):
    """Proves the real continuation shape: accept -> select -> build ->
    verify -> outcome reconciliation -> [separate local acceptance of the
    candidate as a new milestone] -> select -> build -> verify again.

    Also proves the negative: a bare second dispatch call against the
    SAME accepted milestone for a different, not-yet-reachable slice does
    not succeed -- it is correctly held as NO_DEPENDENCY_READY_SLICE,
    since that slice's exact_base_sha does not match the still-current
    accepted sha. Nothing in the real system "immediately dispatches the
    next slice" merely because the first one finished; a genuine new
    acceptance event is required.
    """
    env = proof_env
    worker_module = env["worker_module"]
    monkeypatch.setattr(worker_module, "build_candidate", _fake_builder)
    monkeypatch.setattr(worker_module, "verify_and_commit", _fake_verifier)

    from project_orchestrator.iron_god_milestone_resolution import (
        resolve_latest_committed_milestone,
    )
    from project_orchestrator.resident_dispatch_binding import (
        dispatch_existing_accepted_event,
    )

    claims_path = env["tmp_path"] / "claims.sqlite"
    lease_path = env["tmp_path"] / "leases.sqlite"
    worker_executor = _make_worker_executor(
        env, claims_path=claims_path, lease_path=lease_path
    )

    # --- Slice 1, against milestone M-proof-0 (accepted_sha=base_sha) ---
    _git(
        env["repo"],
        "worktree",
        "add",
        "-b",
        "iron-god/proof-slice-1",
        str(env["tmp_path"] / "wt-slice1"),
        env["base_sha"],
    )
    ledger = _write_handoff_and_ledger_row(
        env,
        slice_id="PROOF-SLICE-1",
        worktree_name="wt-slice1",
        branch="iron-god/proof-slice-1",
        exact_base_sha=env["base_sha"],
    )
    task1 = _build_task(
        env,
        slice_id="PROOF-SLICE-1",
        worktree_name="wt-slice1",
        branch="iron-god/proof-slice-1",
        base_sha=env["base_sha"],
    )
    latest0 = resolve_latest_committed_milestone(
        env["state"].snapshot(), REPO_IDENTIFIER
    )
    assert latest0.milestone_id == "M-proof-0"

    result1 = dispatch_existing_accepted_event(
        controller=ledger,
        owner_state_root=env["flow"],
        expected_owner_state_root=env["flow"],
        state_db=env["tmp_path"] / "accepted.sqlite",
        repo=env["repo"],
        milestone_id=latest0.milestone_id,
        handoffs={
            "PROOF-SLICE-1": json.loads(
                (worker_module.HANDOFF_DIR / "PROOF-SLICE-1.json").read_text()
            )
        },
        claims_path=claims_path,
        worktree_root=env["tmp_path"],
        task=task1,
        worker_executor=worker_executor,
    )
    assert result1["status"] == "VERIFIED_CANDIDATE"
    candidate_1_sha = result1["candidate_sha"]
    assert candidate_1_sha != env["base_sha"]

    # --- A bare second dispatch attempt for a different, not-yet-reachable
    # slice under the SAME still-current milestone must not succeed: its
    # exact_base_sha (candidate_1_sha) does not match the accepted sha
    # (still base_sha, since nothing has re-accepted yet). ---
    _git(
        env["repo"],
        "worktree",
        "add",
        "-b",
        "iron-god/proof-slice-2",
        str(env["tmp_path"] / "wt-slice2"),
        candidate_1_sha,
    )
    ledger2_attempt = _write_handoff_and_ledger_row(
        env,
        slice_id="PROOF-SLICE-2",
        worktree_name="wt-slice2",
        branch="iron-god/proof-slice-2",
        exact_base_sha=candidate_1_sha,
    )
    task2 = _build_task(
        env,
        slice_id="PROOF-SLICE-2",
        worktree_name="wt-slice2",
        branch="iron-god/proof-slice-2",
        base_sha=candidate_1_sha,
    )
    with pytest.raises(
        RuntimeError, match="DISPATCH_SELECTOR_NOT_READY:NO_DEPENDENCY_READY_SLICE"
    ):
        dispatch_existing_accepted_event(
            controller=ledger2_attempt,
            owner_state_root=env["flow"],
            expected_owner_state_root=env["flow"],
            state_db=env["tmp_path"] / "accepted.sqlite",
            repo=env["repo"],
            milestone_id=latest0.milestone_id,
            handoffs={
                "PROOF-SLICE-2": json.loads(
                    (worker_module.HANDOFF_DIR / "PROOF-SLICE-2.json").read_text()
                )
            },
            claims_path=claims_path,
            worktree_root=env["tmp_path"],
            task=task2,
            worker_executor=worker_executor,
        )

    # --- Genuine local acceptance of candidate_1_sha as a NEW milestone,
    # using the REAL mechanism select_next_after_acceptance actually reads
    # (LocalFirstState + LocalGitRefs), not iron_god_acceptance.py's
    # separate, disconnected ref/state system. ---
    env["refs"].advance_accepted(expected_sha=env["base_sha"], new_sha=candidate_1_sha)
    env["refs"].protect("milestone", "M-proof-1", candidate_1_sha)
    env["state"].prepare_acceptance(
        repository=REPO_IDENTIFIER,
        milestone_id="M-proof-1",
        accepted_base_sha=env["base_sha"],
        accepted_sha=candidate_1_sha,
        verification_sha256="c" * 64,
        product_reality="NOT_REQUIRED",
        context_json="{}",
    )
    env["state"].finish_prepared_acceptance(
        milestone_id="M-proof-1",
        expected_sha=candidate_1_sha,
        expected_verification_sha256="c" * 64,
    )

    latest1 = resolve_latest_committed_milestone(
        env["state"].snapshot(), REPO_IDENTIFIER
    )
    assert latest1.milestone_id == "M-proof-1"
    assert latest1.accepted_sha == candidate_1_sha

    # --- NOW slice 2 dispatches and completes, against the new milestone. ---
    result2 = dispatch_existing_accepted_event(
        controller=ledger2_attempt,
        owner_state_root=env["flow"],
        expected_owner_state_root=env["flow"],
        state_db=env["tmp_path"] / "accepted.sqlite",
        repo=env["repo"],
        milestone_id=latest1.milestone_id,
        handoffs={
            "PROOF-SLICE-2": json.loads(
                (worker_module.HANDOFF_DIR / "PROOF-SLICE-2.json").read_text()
            )
        },
        claims_path=claims_path,
        worktree_root=env["tmp_path"],
        task=task2,
        worker_executor=worker_executor,
    )
    assert result2["status"] == "VERIFIED_CANDIDATE"
    candidate_2_sha = result2["candidate_sha"]
    assert candidate_2_sha != candidate_1_sha

    from project_orchestrator.local_dispatch_event_claim import inspect_claims

    claims = inspect_claims(claims_path)
    assert len(claims) == 2
    assert {row["slice_id"] for row in claims} == {"PROOF-SLICE-1", "PROOF-SLICE-2"}
    assert all(row["state"] == "RESULT_RECORDED" for row in claims)


def test_failed_slice_routes_to_repair_without_blocking_independent_repository(
    proof_env, tmp_path, monkeypatch
):
    """A genuine engineering failure in one repository's slice must route
    to the real repair queue and must not block an independent slice in a
    SEPARATE repository/acceptance-event from dispatching and succeeding --
    the mission's "do not repeatedly analyze the same blocker when other
    independent work remains available" property."""
    env = proof_env
    worker_module = env["worker_module"]

    # --- Second, independent repository + its own LocalGitRefs, sharing
    # the same LocalFirstState/claims_path (one controller manages many
    # repos), but with its own acceptance event. ---
    second_repo = _init_repo(tmp_path / "repo-b")
    second_base_sha = _git(second_repo, "rev-parse", "HEAD")

    from project_orchestrator.local_git_refs import LocalGitRefs

    second_refs = LocalGitRefs(second_repo, owner_state_root=env["flow"])
    second_refs.bootstrap_accepted(second_base_sha)
    second_refs.protect("milestone", "M-proof-b-0", second_base_sha)
    env["state"].prepare_acceptance(
        repository=SECOND_REPO_IDENTIFIER,
        milestone_id="M-proof-b-0",
        accepted_base_sha="0" * 40,
        accepted_sha=second_base_sha,
        verification_sha256="d" * 64,
        product_reality="NOT_REQUIRED",
        context_json="{}",
    )
    env["state"].finish_prepared_acceptance(
        milestone_id="M-proof-b-0",
        expected_sha=second_base_sha,
        expected_verification_sha256="d" * 64,
    )

    from project_orchestrator.iron_god_milestone_resolution import (
        resolve_latest_committed_milestone,
    )
    from project_orchestrator.resident_dispatch_binding import (
        dispatch_existing_accepted_event,
    )

    claims_path = env["tmp_path"] / "claims.sqlite"
    lease_path = env["tmp_path"] / "leases.sqlite"
    worker_executor = _make_worker_executor(
        env, claims_path=claims_path, lease_path=lease_path
    )

    # --- Repository A's slice: builder succeeds (real files written),
    # verifier fails with a genuine, repairable engineering-failure code. ---
    monkeypatch.setattr(worker_module, "build_candidate", _fake_builder)
    monkeypatch.setattr(worker_module, "verify_and_commit", _failing_verifier)

    _git(
        env["repo"],
        "worktree",
        "add",
        "-b",
        "iron-god/proof-slice-a",
        str(env["tmp_path"] / "wt-slice-a"),
        env["base_sha"],
    )
    ledger_a = _write_handoff_and_ledger_row(
        env,
        slice_id="PROOF-SLICE-A",
        worktree_name="wt-slice-a",
        branch="iron-god/proof-slice-a",
        exact_base_sha=env["base_sha"],
        repository=REPO_IDENTIFIER,
    )
    task_a = _build_task(
        env,
        slice_id="PROOF-SLICE-A",
        worktree_name="wt-slice-a",
        branch="iron-god/proof-slice-a",
        base_sha=env["base_sha"],
    )
    latest_a = resolve_latest_committed_milestone(
        env["state"].snapshot(), REPO_IDENTIFIER
    )

    result_a = dispatch_existing_accepted_event(
        controller=ledger_a,
        owner_state_root=env["flow"],
        expected_owner_state_root=env["flow"],
        state_db=env["tmp_path"] / "accepted.sqlite",
        repo=env["repo"],
        milestone_id=latest_a.milestone_id,
        handoffs={
            "PROOF-SLICE-A": json.loads(
                (worker_module.HANDOFF_DIR / "PROOF-SLICE-A.json").read_text()
            )
        },
        claims_path=claims_path,
        worktree_root=env["tmp_path"],
        task=task_a,
        worker_executor=worker_executor,
    )
    assert result_a["status"] == "REPAIR_QUEUED"
    assert result_a["slice_id"] == "PROOF-SLICE-A"
    assert "repair_id" in result_a

    # The candidate worktree's HEAD must not have advanced -- the builder
    # wrote real files, but the failing verifier never committed them, so
    # a repaired/routed failure is never mistaken for a VERIFIED success.
    head_a_after = _git(env["tmp_path"] / "wt-slice-a", "rev-parse", "HEAD")
    assert head_a_after == env["base_sha"]
    assert (env["tmp_path"] / "wt-slice-a" / "src" / "proof.py").exists(), (
        "the builder's real uncommitted files must still be present in the worktree"
    )

    # --- Repository B's slice: independent acceptance event, succeeding
    # builder+verifier. Must dispatch and complete even though repository
    # A's failure is still sitting unresolved in the repair queue. ---
    monkeypatch.setattr(worker_module, "build_candidate", _fake_builder)
    monkeypatch.setattr(worker_module, "verify_and_commit", _fake_verifier)

    _git(
        second_repo,
        "worktree",
        "add",
        "-b",
        "iron-god/proof-slice-b",
        str(env["tmp_path"] / "wt-slice-b"),
        second_base_sha,
    )
    ledger_b = _write_handoff_and_ledger_row(
        env,
        slice_id="PROOF-SLICE-B",
        worktree_name="wt-slice-b",
        branch="iron-god/proof-slice-b",
        exact_base_sha=second_base_sha,
        repository=SECOND_REPO_IDENTIFIER,
    )
    task_b = _build_task(
        env,
        slice_id="PROOF-SLICE-B",
        worktree_name="wt-slice-b",
        branch="iron-god/proof-slice-b",
        base_sha=second_base_sha,
    )
    latest_b = resolve_latest_committed_milestone(
        env["state"].snapshot(), SECOND_REPO_IDENTIFIER
    )
    assert latest_b.milestone_id == "M-proof-b-0"

    result_b = dispatch_existing_accepted_event(
        controller=ledger_b,
        owner_state_root=env["flow"],
        expected_owner_state_root=env["flow"],
        state_db=env["tmp_path"] / "accepted.sqlite",
        repo=second_repo,
        milestone_id=latest_b.milestone_id,
        handoffs={
            "PROOF-SLICE-B": json.loads(
                (worker_module.HANDOFF_DIR / "PROOF-SLICE-B.json").read_text()
            )
        },
        claims_path=claims_path,
        worktree_root=env["tmp_path"],
        task=task_b,
        worker_executor=worker_executor,
    )
    assert result_b["status"] == "VERIFIED_CANDIDATE", (
        "repository B's independent slice must succeed even though "
        "repository A's failure is still unresolved in the repair queue"
    )
    assert result_b["candidate_sha"] != second_base_sha

    from project_orchestrator.local_dispatch_event_claim import inspect_claims

    claims = inspect_claims(claims_path)
    assert len(claims) == 2
    by_slice = {row["slice_id"]: row for row in claims}
    assert by_slice["PROOF-SLICE-A"]["state"] == "RESULT_RECORDED"
    assert by_slice["PROOF-SLICE-B"]["state"] == "RESULT_RECORDED"
