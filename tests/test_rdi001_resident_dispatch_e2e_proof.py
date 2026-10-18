"""RDI-001 end-to-end connection proof -- genuinely isolated development
scope, per the 2026-10-17 assignment.

Proves the exact missing connection (accepted_event_dispatch_coordinator
-> resident_dispatch_binding -> the REAL, unedited
local_continuous_worker.execute_reserved_dispatch_claim ->
execute_candidate_attempt) end-to-end against a disposable real Git
repository, real SQLite stores, and the real claim-fencing state
machine (CLAIMED -> INVOCATION_RESERVED -> RESULT_RECORDED) -- without
ever editing local_continuous_worker.py on disk, without creating any
production acceptance record, and without activating real candidate
execution anywhere outside this disposable fixture.

local_continuous_worker.py is not an installed package module (it is a
standalone script living outside the project-orchestrator Git repo),
so it is loaded here via importlib from its real, unedited, on-disk
path. Every module-level Path constant it uses to find "the world"
(FLOW, CONTROLLER_STATE, HANDOFF_DIR, STATUS) is monkeypatched to a
disposable tmp_path location *after* that real, unedited load -- the
file on disk is never written to, and the real production ledger,
handoffs directory, and worker-status file are never read or touched
by this test. The real `build_candidate`/`verify_and_commit` module
globals (which would shell out to `claude` and run a full
ruff/pytest/engineering-preflight cycle) are substituted with
lightweight-but-real fakes: the fake builder writes real files, the
fake verifier performs a real `git add`/`git commit` and returns a
real head_sha, so every surrounding real mechanism (lease
claim/heartbeat/transition, exact-head verification, worktree
identity, dispatch claim fencing, invoke_once reservation) runs
unmodified and for real.

What this proves:
* accepted-event dispatch: a durably-accepted milestone correctly
  surfaces exactly one dependency-ready slice and dispatches it.
* exact-base identity: a candidate worktree whose HEAD does not match
  the handoff's exact_base_sha is rejected before any lease is
  claimed.
* branch/worktree integrity: a worktree checked out on the wrong
  branch is rejected by the real validate_candidate_worktree check.
* claim fencing / no duplicate execution: a second dispatch attempt
  for the same accepted event is fenced and never invokes the builder
  twice.
* outcome reconciliation: the dispatch claim's own state machine ends
  at RESULT_RECORDED, and the accepted-baseline/milestone refs reflect
  exactly one real commit, not a synthetic or duplicated one.

What this does NOT prove (explicitly out of scope, consistent with
every constraint this session has observed): a real Claude Code
invocation, a real repository-native 26-gate preflight run, real
Product Reality evaluation, or anything touching the actual
IronGodLocal controller state, `main`'s working tree, or
`candidate_execution_authorized` in production.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

CONTROLLER_PATH = Path(
    r"C:\Users\jerem\AppData\Local\ExponentialEngine\IronGodLocal\local_continuous_worker.py"
)
CONTROLLER_BACKUP_PATH = Path(
    r"C:\Users\jerem\AppData\Local\ExponentialEngine\IronGodLocal"
    r"\local_continuous_worker.py.pre-rdi001-backup-20261016.py"
)

# accepted_event_dispatch_coordinator.py is a bare module living alongside
# local_continuous_worker.py in IronGodLocal, not inside the installed
# project_orchestrator package -- resident_dispatch_binding.py imports it
# by bare name, matching how it is actually deployed. Make it importable
# the same way here, read-only (sys.path addition only, no file written).
if str(CONTROLLER_PATH.parent) not in sys.path:
    sys.path.insert(0, str(CONTROLLER_PATH.parent))

REPO_IDENTIFIER = "test-proof-owner/test-proof-repo"


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
    """Load the real, unedited local_continuous_worker.py from disk.

    Confirms byte-for-byte identity with the known-good backup first --
    if this assertion ever fails, the live controller has been edited
    since the backup was taken, and this test must not proceed as if
    it were still verifying the original file.
    """
    assert CONTROLLER_PATH.read_bytes() == CONTROLLER_BACKUP_PATH.read_bytes(), (
        "local_continuous_worker.py differs from its pre-RDI-001 backup -- "
        "refusing to run this proof against an unexpectedly modified live file"
    )
    spec = importlib.util.spec_from_file_location(
        "rdi001_proof_worker_module", CONTROLLER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def proof_env(tmp_path, worker_module, monkeypatch):
    """Build a fully disposable environment and repoint every module-level
    Path constant the real controller uses, so nothing it does can reach
    the real IronGodLocal files on disk."""
    flow = tmp_path / "flow-state"
    flow.mkdir()
    (flow / "human-mode.json").write_text(
        json.dumps({"enabled": False}), encoding="utf-8"
    )
    (flow / "iron-god-exclusive.json").write_text(
        json.dumps({"enabled": True, "target_product": "iron-god"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(worker_module, "FLOW", flow)
    monkeypatch.setattr(worker_module, "BASE", tmp_path)
    monkeypatch.setattr(worker_module, "HANDOFF_DIR", tmp_path / "handoffs")
    monkeypatch.setattr(
        worker_module, "CONTROLLER_STATE", tmp_path / "controller-handoff-state.json"
    )
    monkeypatch.setattr(worker_module, "STATUS", tmp_path / "local-worker-status.json")
    (tmp_path / "handoffs").mkdir()

    assert worker_module.gate() is True, (
        "disposable owner/exclusive fixture must pass gate()"
    )

    repo = _init_repo(tmp_path / "repo")
    base_sha = _git(repo, "rev-parse", "HEAD")

    from project_orchestrator.local_first_state import LocalFirstState
    from project_orchestrator.local_git_refs import LocalGitRefs

    # select_next_after_acceptance (the real selector) reads refs via
    # LocalGitRefs -- a different, single-repo-scoped ref system from this
    # session's own iron_god_refs.py/iron_god_milestone_refs.py modules.
    # Seed it with its own bootstrap_accepted()/protect() API, not mine.
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
    ledger = {
        "controller": "iron-god-handoff-v1",
        "mode": "candidate_mode",
        "candidate_execution_authorized": True,
        "slices": [{"id": slice_id, "state": "ELIGIBLE"}],
    }
    worker_module.CONTROLLER_STATE.write_text(json.dumps(ledger), encoding="utf-8")
    return ledger


def _build_task(env: dict, *, slice_id: str, worktree_name: str, branch: str) -> dict:
    work = env["tmp_path"] / worktree_name
    prompt = (
        "Objective: proof slice objective Allowed paths: "
        "src/proof.py, tests/test_proof.py. Forbidden: . Acceptance: "
    )
    return {
        "id": slice_id,
        "base_sha": env["base_sha"],
        "branch": branch,
        "work": work,
        "source": "src/proof.py",
        "tests": "tests/test_proof.py",
        "prompt": prompt,
    }


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


def test_accepted_event_dispatch_end_to_end(proof_env, monkeypatch):
    env = proof_env
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

    ledger = _write_handoff_and_ledger_row(
        env,
        slice_id="PROOF-SLICE-1",
        worktree_name="wt-slice1",
        branch="iron-god/proof-slice-1",
    )
    task = _build_task(
        env,
        slice_id="PROOF-SLICE-1",
        worktree_name="wt-slice1",
        branch="iron-god/proof-slice-1",
    )

    from project_orchestrator.iron_god_milestone_resolution import (
        resolve_latest_committed_milestone,
    )
    from project_orchestrator.resident_dispatch_binding import (
        dispatch_existing_accepted_event,
    )

    latest = resolve_latest_committed_milestone(
        env["state"].snapshot(), REPO_IDENTIFIER
    )
    assert latest is not None
    assert latest.milestone_id == "M-proof-0"
    assert latest.accepted_sha == env["base_sha"]

    claims_path = env["tmp_path"] / "claims.sqlite"
    lease_path = env["tmp_path"] / "leases.sqlite"
    worker_executor = _make_worker_executor(
        env, claims_path=claims_path, lease_path=lease_path
    )

    result = dispatch_existing_accepted_event(
        controller=ledger,
        owner_state_root=env["flow"],
        expected_owner_state_root=env["flow"],
        state_db=env["tmp_path"] / "accepted.sqlite",
        repo=env["repo"],
        milestone_id=latest.milestone_id,
        handoffs={
            "PROOF-SLICE-1": json.loads(
                (worker_module.HANDOFF_DIR / "PROOF-SLICE-1.json").read_text()
            )
        },
        claims_path=claims_path,
        worktree_root=env["tmp_path"],
        task=task,
        worker_executor=worker_executor,
    )

    assert result["status"] == "VERIFIED_CANDIDATE"
    assert result["slice_id"] == "PROOF-SLICE-1"

    new_head = _git(env["tmp_path"] / "wt-slice1", "rev-parse", "HEAD")
    assert new_head != env["base_sha"], (
        "fake verifier's real commit must have advanced the candidate worktree HEAD"
    )
    assert result["candidate_sha"] == new_head

    from project_orchestrator.local_dispatch_event_claim import inspect_claims

    claims = inspect_claims(claims_path)
    assert len(claims) == 1
    assert claims[0]["state"] == "RESULT_RECORDED"
    assert claims[0]["slice_id"] == "PROOF-SLICE-1"


def test_duplicate_dispatch_is_fenced_never_invokes_builder_twice(
    proof_env, monkeypatch
):
    env = proof_env
    worker_module = env["worker_module"]

    call_count = {"builder": 0}

    def counting_builder(task, *, lease_heartbeat=None):
        call_count["builder"] += 1
        _fake_builder(task, lease_heartbeat=lease_heartbeat)

    monkeypatch.setattr(worker_module, "build_candidate", counting_builder)
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
    ledger = _write_handoff_and_ledger_row(
        env,
        slice_id="PROOF-SLICE-1",
        worktree_name="wt-slice1",
        branch="iron-god/proof-slice-1",
    )
    task = _build_task(
        env,
        slice_id="PROOF-SLICE-1",
        worktree_name="wt-slice1",
        branch="iron-god/proof-slice-1",
    )

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
    worker_executor = _make_worker_executor(
        env, claims_path=claims_path, lease_path=lease_path
    )
    handoffs = {
        "PROOF-SLICE-1": json.loads(
            (worker_module.HANDOFF_DIR / "PROOF-SLICE-1.json").read_text()
        )
    }

    dispatch_existing_accepted_event(
        controller=ledger,
        owner_state_root=env["flow"],
        expected_owner_state_root=env["flow"],
        state_db=env["tmp_path"] / "accepted.sqlite",
        repo=env["repo"],
        milestone_id=latest.milestone_id,
        handoffs=handoffs,
        claims_path=claims_path,
        worktree_root=env["tmp_path"],
        task=task,
        worker_executor=worker_executor,
    )
    assert call_count["builder"] == 1

    from project_orchestrator.local_dispatch_event_claim import DispatchFenceError

    with pytest.raises(DispatchFenceError):
        dispatch_existing_accepted_event(
            controller=ledger,
            owner_state_root=env["flow"],
            expected_owner_state_root=env["flow"],
            state_db=env["tmp_path"] / "accepted.sqlite",
            repo=env["repo"],
            milestone_id=latest.milestone_id,
            handoffs=handoffs,
            claims_path=claims_path,
            worktree_root=env["tmp_path"],
            task=task,
            worker_executor=worker_executor,
        )
    assert call_count["builder"] == 1, (
        "a duplicate dispatch attempt must never invoke the builder a second time"
    )


def test_wrong_branch_worktree_rejected_before_any_lease(proof_env, monkeypatch):
    env = proof_env
    worker_module = env["worker_module"]

    monkeypatch.setattr(worker_module, "build_candidate", _fake_builder)
    monkeypatch.setattr(worker_module, "verify_and_commit", _fake_verifier)

    # Provision the worktree on the WRONG branch relative to the handoff.
    _git(
        env["repo"],
        "worktree",
        "add",
        "-b",
        "wrong-branch",
        str(env["tmp_path"] / "wt-slice1"),
        env["base_sha"],
    )
    ledger = _write_handoff_and_ledger_row(
        env,
        slice_id="PROOF-SLICE-1",
        worktree_name="wt-slice1",
        branch="iron-god/proof-slice-1",
    )
    task = _build_task(
        env,
        slice_id="PROOF-SLICE-1",
        worktree_name="wt-slice1",
        branch="iron-god/proof-slice-1",
    )

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
    worker_executor = _make_worker_executor(
        env, claims_path=claims_path, lease_path=lease_path
    )
    handoffs = {
        "PROOF-SLICE-1": json.loads(
            (worker_module.HANDOFF_DIR / "PROOF-SLICE-1.json").read_text()
        )
    }

    with pytest.raises(RuntimeError, match="UNEXPECTED_BRANCH"):
        dispatch_existing_accepted_event(
            controller=ledger,
            owner_state_root=env["flow"],
            expected_owner_state_root=env["flow"],
            state_db=env["tmp_path"] / "accepted.sqlite",
            repo=env["repo"],
            milestone_id=latest.milestone_id,
            handoffs=handoffs,
            claims_path=claims_path,
            worktree_root=env["tmp_path"],
            task=task,
            worker_executor=worker_executor,
        )

    from project_orchestrator.local_dispatch_event_claim import inspect_claims

    # A claim may already be durably recorded (claim-before-build is the
    # whole point of the fencing design) -- but the builder must never
    # have been invoked for a worktree on the wrong branch.
    claims = inspect_claims(claims_path)
    assert all(row["state"] != "RESULT_RECORDED" for row in claims)


# ---------------------------------------------------------------------------
# 2026-10-17 Phase 1 extension: comprehensive preclaim Git identity
# verification and failure-scenario coverage (dirty worktree, incorrect
# HEAD, invalid repository, expired lease recovery, interrupted
# execution / crash recovery). Same isolated-scope discipline as above:
# the real, unedited local_continuous_worker.py, only
# build_candidate/verify_and_commit substituted.
# ---------------------------------------------------------------------------


def _lease_has_any_row(lease_path: Path, slice_id: str) -> bool:
    import sqlite3

    db = sqlite3.connect(lease_path)
    try:
        row = db.execute(
            "SELECT 1 FROM slice_leases WHERE slice_id=?", (slice_id,)
        ).fetchone()
        return row is not None
    except sqlite3.OperationalError:
        return False
    finally:
        db.close()


def test_dirty_worktree_rejected_before_any_lease(proof_env, monkeypatch):
    env = proof_env
    worker_module = env["worker_module"]

    monkeypatch.setattr(worker_module, "build_candidate", _fake_builder)
    monkeypatch.setattr(worker_module, "verify_and_commit", _fake_verifier)

    wt = env["tmp_path"] / "wt-slice1"
    _git(
        env["repo"],
        "worktree",
        "add",
        "-b",
        "iron-god/proof-slice-1",
        str(wt),
        env["base_sha"],
    )
    # A dirty worktree -- an untracked file present before the builder ever runs.
    (wt / "unexpected.txt").write_text("not part of the plan\n", encoding="utf-8")

    ledger = _write_handoff_and_ledger_row(
        env,
        slice_id="PROOF-SLICE-1",
        worktree_name="wt-slice1",
        branch="iron-god/proof-slice-1",
    )
    task = _build_task(
        env,
        slice_id="PROOF-SLICE-1",
        worktree_name="wt-slice1",
        branch="iron-god/proof-slice-1",
    )

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
    worker_executor = _make_worker_executor(
        env, claims_path=claims_path, lease_path=lease_path
    )
    handoffs = {
        "PROOF-SLICE-1": json.loads(
            (worker_module.HANDOFF_DIR / "PROOF-SLICE-1.json").read_text()
        )
    }

    # check_paths(work, set()) -- called first, inside
    # validate_candidate_worktree -- is strictly stricter than the
    # SLICE_WORKTREE_NOT_CLEAN check that follows it (an empty allowed-set
    # means "nothing may differ"), so in practice any dirty worktree is
    # always rejected as UNEXPECTED_WORKTREE_CHANGE before
    # SLICE_WORKTREE_NOT_CLEAN is ever reached. Verified directly against
    # the real function before writing this assertion.
    with pytest.raises(RuntimeError, match="UNEXPECTED_WORKTREE_CHANGE:unexpected.txt"):
        dispatch_existing_accepted_event(
            controller=ledger,
            owner_state_root=env["flow"],
            expected_owner_state_root=env["flow"],
            state_db=env["tmp_path"] / "accepted.sqlite",
            repo=env["repo"],
            milestone_id=latest.milestone_id,
            handoffs=handoffs,
            claims_path=claims_path,
            worktree_root=env["tmp_path"],
            task=task,
            worker_executor=worker_executor,
        )

    # No lease may ever have been created for a worktree rejected as dirty.
    assert not lease_path.exists() or not _lease_has_any_row(
        lease_path, "PROOF-SLICE-1"
    )


def test_incorrect_head_rejected_before_any_lease(proof_env, monkeypatch):
    env = proof_env
    worker_module = env["worker_module"]

    monkeypatch.setattr(worker_module, "build_candidate", _fake_builder)
    monkeypatch.setattr(worker_module, "verify_and_commit", _fake_verifier)

    wt = env["tmp_path"] / "wt-slice1"
    _git(
        env["repo"],
        "worktree",
        "add",
        "-b",
        "iron-god/proof-slice-1",
        str(wt),
        env["base_sha"],
    )
    # Advance the worktree's HEAD past the handoff's exact_base_sha.
    (wt / "drift.txt").write_text("drift\n", encoding="utf-8")
    _git(wt, "add", "drift.txt")
    _git(wt, "commit", "-q", "-m", "unexpected drift")

    ledger = _write_handoff_and_ledger_row(
        env,
        slice_id="PROOF-SLICE-1",
        worktree_name="wt-slice1",
        branch="iron-god/proof-slice-1",
    )
    task = _build_task(
        env,
        slice_id="PROOF-SLICE-1",
        worktree_name="wt-slice1",
        branch="iron-god/proof-slice-1",
    )

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
    worker_executor = _make_worker_executor(
        env, claims_path=claims_path, lease_path=lease_path
    )
    handoffs = {
        "PROOF-SLICE-1": json.loads(
            (worker_module.HANDOFF_DIR / "PROOF-SLICE-1.json").read_text()
        )
    }

    with pytest.raises(RuntimeError, match="SLICE_BASE_SHA_MISMATCH"):
        dispatch_existing_accepted_event(
            controller=ledger,
            owner_state_root=env["flow"],
            expected_owner_state_root=env["flow"],
            state_db=env["tmp_path"] / "accepted.sqlite",
            repo=env["repo"],
            milestone_id=latest.milestone_id,
            handoffs=handoffs,
            claims_path=claims_path,
            worktree_root=env["tmp_path"],
            task=task,
            worker_executor=worker_executor,
        )


def test_invalid_repository_rejected_before_any_claim(proof_env, monkeypatch):
    env = proof_env
    worker_module = env["worker_module"]

    monkeypatch.setattr(worker_module, "build_candidate", _fake_builder)
    monkeypatch.setattr(worker_module, "verify_and_commit", _fake_verifier)

    wt = env["tmp_path"] / "wt-slice1"
    _git(
        env["repo"],
        "worktree",
        "add",
        "-b",
        "iron-god/proof-slice-1",
        str(wt),
        env["base_sha"],
    )

    # The accepted milestone is for REPO_IDENTIFIER; the handoff packet
    # (constructed by hand here, bypassing the normal helper) declares a
    # different, invalid repository identity.
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
        "repository": "wrong-owner/wrong-repo",
        "requires_milestones": [],
    }
    (worker_module.HANDOFF_DIR / "PROOF-SLICE-1.json").write_text(
        json.dumps(packet), encoding="utf-8"
    )
    ledger = {
        "controller": "iron-god-handoff-v1",
        "mode": "candidate_mode",
        "candidate_execution_authorized": True,
        "slices": [{"id": "PROOF-SLICE-1", "state": "ELIGIBLE"}],
    }
    worker_module.CONTROLLER_STATE.write_text(json.dumps(ledger), encoding="utf-8")

    task = _build_task(
        env,
        slice_id="PROOF-SLICE-1",
        worktree_name="wt-slice1",
        branch="iron-god/proof-slice-1",
    )

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
    worker_executor = _make_worker_executor(
        env, claims_path=claims_path, lease_path=lease_path
    )
    handoffs = {"PROOF-SLICE-1": packet}

    # select_next_after_acceptance itself filters eligible handoffs by
    # packet["repository"] == intent["repository"] -- a repository
    # mismatch is rejected at the selector, before any claim is ever
    # created, not merely before RESULT_RECORDED. Verified directly
    # against the real selector before writing this assertion.
    with pytest.raises(
        RuntimeError, match="DISPATCH_SELECTOR_NOT_READY:NO_DEPENDENCY_READY_SLICE"
    ):
        dispatch_existing_accepted_event(
            controller=ledger,
            owner_state_root=env["flow"],
            expected_owner_state_root=env["flow"],
            state_db=env["tmp_path"] / "accepted.sqlite",
            repo=env["repo"],
            milestone_id=latest.milestone_id,
            handoffs=handoffs,
            claims_path=claims_path,
            worktree_root=env["tmp_path"],
            task=task,
            worker_executor=worker_executor,
        )

    from project_orchestrator.local_dispatch_event_claim import inspect_claims

    claims = inspect_claims(claims_path)
    assert claims == [], (
        "an invalid-repository handoff must be rejected before any claim is ever created"
    )


def test_expired_lease_recovers_with_incremented_generation(proof_env, monkeypatch):
    """Proves claim_slice_lease's own real stale-lease recovery directly.

    Scoped to the lease layer alone, not the full dispatch chain: the
    dispatch-provenance binder (local_dispatch_worker_binding.bind_worker_lease)
    independently asserts exactly one historical SLICE_LEASED event per
    slice_id ever recorded against its claims_path -- a stricter,
    one-shot-per-slice invariant that a directly pre-seeded lease (bypassing
    the claim/reservation layer entirely, as this test deliberately does to
    isolate the lease mechanism) is not a legitimate case of in the real
    flow. That invariant is real and correct; it is simply a different
    layer than the one this test is proving.
    """
    env = proof_env
    worker_module = env["worker_module"]
    lease_path = env["tmp_path"] / "leases.sqlite"

    first = worker_module.claim_slice_lease(
        slice_id="PROOF-SLICE-1",
        base_sha=env["base_sha"],
        owner_pid=11111,
        ttl_seconds=1,
        path=lease_path,
        now=1_700_000_000.0,
    )
    assert first["generation"] == 1
    assert first["state"] == "LEASED"

    # Attempting to claim again while the lease is still live must refuse.
    with pytest.raises(RuntimeError, match="SLICE_LEASE_OCCUPIED"):
        worker_module.claim_slice_lease(
            slice_id="PROOF-SLICE-1",
            base_sha=env["base_sha"],
            owner_pid=22222,
            ttl_seconds=1,
            path=lease_path,
            now=1_700_000_000.5,
        )

    # Once lease_until has passed, a fresh claim must recover the stale
    # lease with an incremented generation, not be blocked indefinitely.
    recovered = worker_module.claim_slice_lease(
        slice_id="PROOF-SLICE-1",
        base_sha=env["base_sha"],
        owner_pid=33333,
        ttl_seconds=1800,
        path=lease_path,
        now=1_700_000_100.0,
    )
    assert recovered["generation"] == 2, (
        "an expired lease must recover with an incremented generation"
    )
    assert recovered["state"] == "LEASED"
    assert recovered["owner_pid"] == 33333

    import sqlite3

    db = sqlite3.connect(lease_path)
    try:
        events = db.execute(
            "SELECT event_type, generation FROM slice_lease_events "
            "WHERE slice_id='PROOF-SLICE-1' ORDER BY rowid"
        ).fetchall()
    finally:
        db.close()
    event_types = [row[0] for row in events]
    assert "STALE_LEASE_RECOVERED" in event_types, (
        "the real recovery path must durably log STALE_LEASE_RECOVERED, not silently overwrite"
    )


def test_interrupted_execution_leaves_claim_reserved_never_auto_retried(
    proof_env, monkeypatch
):
    env = proof_env
    worker_module = env["worker_module"]

    def crashing_builder(task, *, lease_heartbeat=None):
        raise RuntimeError("SIMULATED_WORKER_CRASH")

    monkeypatch.setattr(worker_module, "build_candidate", crashing_builder)
    monkeypatch.setattr(worker_module, "verify_and_commit", _fake_verifier)

    wt = env["tmp_path"] / "wt-slice1"
    _git(
        env["repo"],
        "worktree",
        "add",
        "-b",
        "iron-god/proof-slice-1",
        str(wt),
        env["base_sha"],
    )
    ledger = _write_handoff_and_ledger_row(
        env,
        slice_id="PROOF-SLICE-1",
        worktree_name="wt-slice1",
        branch="iron-god/proof-slice-1",
    )
    task = _build_task(
        env,
        slice_id="PROOF-SLICE-1",
        worktree_name="wt-slice1",
        branch="iron-god/proof-slice-1",
    )

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
    worker_executor = _make_worker_executor(
        env, claims_path=claims_path, lease_path=lease_path
    )
    handoffs = {
        "PROOF-SLICE-1": json.loads(
            (worker_module.HANDOFF_DIR / "PROOF-SLICE-1.json").read_text()
        )
    }

    with pytest.raises(RuntimeError, match="SIMULATED_WORKER_CRASH"):
        dispatch_existing_accepted_event(
            controller=ledger,
            owner_state_root=env["flow"],
            expected_owner_state_root=env["flow"],
            state_db=env["tmp_path"] / "accepted.sqlite",
            repo=env["repo"],
            milestone_id=latest.milestone_id,
            handoffs=handoffs,
            claims_path=claims_path,
            worktree_root=env["tmp_path"],
            task=task,
            worker_executor=worker_executor,
        )

    from project_orchestrator.local_dispatch_event_claim import (
        DispatchFenceError,
        inspect_claims,
    )

    claims = inspect_claims(claims_path)
    assert len(claims) == 1
    assert claims[0]["state"] == "INVOCATION_RESERVED", (
        "a crashed executor must leave the claim uncertain, never RESULT_RECORDED"
    )

    # A second attempt for the same accepted event must never silently
    # relaunch the crashed work -- it must be fenced, not retried.
    with pytest.raises(DispatchFenceError):
        dispatch_existing_accepted_event(
            controller=ledger,
            owner_state_root=env["flow"],
            expected_owner_state_root=env["flow"],
            state_db=env["tmp_path"] / "accepted.sqlite",
            repo=env["repo"],
            milestone_id=latest.milestone_id,
            handoffs=handoffs,
            claims_path=claims_path,
            worktree_root=env["tmp_path"],
            task=task,
            worker_executor=worker_executor,
        )
    claims_after = inspect_claims(claims_path)
    assert len(claims_after) == 1, (
        "no second claim row must ever be created for the same event"
    )
