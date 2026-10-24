"""Proves the new read-only commissioning diagnostics (2026-10-18),
and separately proves, directly against the real claim machinery, that
no claim-recovery transition exists today: _transition rejects any
target state outside the fixed three (CLAIMED/INVOCATION_RESERVED/
RESULT_RECORDED), and claim_accepted_next unconditionally fences a
retry against an already-claimed (acceptance_event_id, slice_id) pair
regardless of that claim's current state -- confirming empirically
that fixing the authorization boundary alone would not, by itself,
allow a retry of an interrupted claim.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from project_orchestrator.iron_god_commissioning_diagnostics import (
    diagnose_candidate_authorization,
    diagnose_claim_lifecycle,
)
from project_orchestrator.local_dispatch_event_claim import (
    DispatchClaim,
    DispatchFenceError,
    _transition,
    claim_accepted_next,
)
from project_orchestrator.local_first_state import LocalFirstState
from project_orchestrator.local_git_refs import LocalGitRefs

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


# --- diagnose_candidate_authorization ---------------------------------


def test_diagnosis_reports_all_predicates_ok(tmp_path):
    controller_state = tmp_path / "controller-handoff-state.json"
    controller_state.write_text(
        json.dumps({"mode": "candidate_mode", "candidate_execution_authorized": True}),
        encoding="utf-8",
    )
    diagnosis = diagnose_candidate_authorization(
        controller_state_path=controller_state, gate_fn=lambda: True
    )
    assert diagnosis.overall_authorized is True
    assert diagnosis.blocking_predicates == ()


def test_diagnosis_identifies_mode_as_the_exact_blocking_predicate(tmp_path):
    controller_state = tmp_path / "controller-handoff-state.json"
    controller_state.write_text(
        json.dumps(
            {"mode": "shadow_state_only", "candidate_execution_authorized": False}
        ),
        encoding="utf-8",
    )
    diagnosis = diagnose_candidate_authorization(
        controller_state_path=controller_state, gate_fn=lambda: True
    )
    assert diagnosis.overall_authorized is False
    assert any(
        "mode is 'shadow_state_only'" in p for p in diagnosis.blocking_predicates
    )
    assert any(
        "candidate_execution_authorized is False" in p
        for p in diagnosis.blocking_predicates
    )
    # gate() was True -- must NOT be listed as a blocking predicate.
    assert not any("gate()" in p for p in diagnosis.blocking_predicates)


def test_diagnosis_identifies_gate_as_the_exact_blocking_predicate(tmp_path):
    """Reproduces the owner's exact second-failure scenario in
    isolation: mode/candidate_execution_authorized are correctly
    authorized, but gate() is False -- the diagnosis must name gate(),
    not the file fields, as the blocker."""
    controller_state = tmp_path / "controller-handoff-state.json"
    controller_state.write_text(
        json.dumps({"mode": "candidate_mode", "candidate_execution_authorized": True}),
        encoding="utf-8",
    )
    diagnosis = diagnose_candidate_authorization(
        controller_state_path=controller_state, gate_fn=lambda: False
    )
    assert diagnosis.overall_authorized is False
    assert diagnosis.blocking_predicates == (
        "gate() returned False (owner control active or unverified)",
    )


def test_diagnosis_reproduces_the_owners_exact_commissioning_failure(tmp_path):
    """The real shape: the real persistent file at rest
    (shadow_state_only/False) with gate()==True (owner control not
    active) -- the diagnosis must name ONLY the two file fields as
    blocking, confirming this is precisely what the owner's second
    attempt hit."""
    controller_state = tmp_path / "controller-handoff-state.json"
    controller_state.write_text(
        json.dumps(
            {"mode": "shadow_state_only", "candidate_execution_authorized": False}
        ),
        encoding="utf-8",
    )
    diagnosis = diagnose_candidate_authorization(
        controller_state_path=controller_state, gate_fn=lambda: True
    )
    assert diagnosis.overall_authorized is False
    assert len(diagnosis.blocking_predicates) == 2
    assert diagnosis.gate_result is True


def test_diagnosis_unreadable_file_reports_cleanly(tmp_path):
    diagnosis = diagnose_candidate_authorization(
        controller_state_path=tmp_path / "does-not-exist.json", gate_fn=lambda: True
    )
    assert diagnosis.overall_authorized is False
    assert diagnosis.controller_state_readable is False


# --- diagnose_claim_lifecycle ------------------------------------------


def test_claim_lifecycle_reports_nothing_for_a_slice_with_no_claim(tmp_path):
    diagnosis = diagnose_claim_lifecycle(
        claims_path=tmp_path / "claims.sqlite",
        lease_path=tmp_path / "leases.sqlite",
        slice_id="NEVER-CLAIMED",
    )
    assert diagnosis.claim_exists is False
    assert diagnosis.lease_db_exists is False
    assert diagnosis.builder_outcome_recorded is False


def test_claim_lifecycle_distinguishes_claimed_from_leased_from_executed(tmp_path):
    """Real end-to-end setup: a real claim reaching INVOCATION_RESERVED
    with NO lease ever created (the owner's exact second-attempt
    shape) must be clearly distinguished from a slice that also got a
    real lease and a real recorded outcome."""
    repo = _init_repo(tmp_path / "repo")
    base_sha = _git(repo, "rev-parse", "HEAD")
    flow = tmp_path / "flow-state"
    flow.mkdir()
    (flow / "human-mode.json").write_text('{"enabled": false}', encoding="utf-8")
    (flow / "iron-god-exclusive.json").write_text(
        '{"enabled": true, "target_product": "iron-god"}', encoding="utf-8"
    )

    refs = LocalGitRefs(repo, owner_state_root=flow)
    refs.bootstrap_accepted(base_sha)
    refs.protect("milestone", "M-0", base_sha)
    state = LocalFirstState(tmp_path / "accepted.sqlite")
    state.prepare_acceptance(
        repository=REPO_IDENTIFIER,
        milestone_id="M-0",
        accepted_base_sha="0" * 40,
        accepted_sha=base_sha,
        verification_sha256="a" * 64,
        product_reality="NOT_REQUIRED",
        context_json="{}",
    )
    state.finish_prepared_acceptance(
        milestone_id="M-0", expected_sha=base_sha, expected_verification_sha256="a" * 64
    )

    handoffs = {
        "PROOF-SLICE-1": {
            "schema_version": 1,
            "slice_id": "PROOF-SLICE-1",
            "state": "ELIGIBLE",
            "worktree_name": "wt-1",
            "branch": "iron-god/proof-1",
            "exact_base_sha": base_sha,
            "repository": REPO_IDENTIFIER,
            "requires_milestones": [],
        }
    }
    controller = {
        "mode": "candidate_mode",
        "candidate_execution_authorized": True,
        "slices": [{"id": "PROOF-SLICE-1", "state": "ELIGIBLE"}],
    }
    claims_path = tmp_path / "claims.sqlite"
    lease_path = tmp_path / "leases.sqlite"

    claim = claim_accepted_next(
        state=state,
        refs=refs,
        owner_state_root=flow,
        milestone_id="M-0",
        controller=controller,
        handoffs=handoffs,
        claims_path=claims_path,
    )
    # Reserve it (matching invoke_once's own CLAIMED -> INVOCATION_RESERVED
    # transition) but never create a lease -- the owner's exact second
    # attempt's own shape.
    _transition(
        claim=claim,
        claims_path=claims_path,
        owner_state_root=flow,
        expected="CLAIMED",
        target="INVOCATION_RESERVED",
    )

    diagnosis = diagnose_claim_lifecycle(
        claims_path=claims_path, lease_path=lease_path, slice_id="PROOF-SLICE-1"
    )
    assert diagnosis.claim_exists is True
    assert diagnosis.claim_state == "INVOCATION_RESERVED"
    assert diagnosis.lease_db_exists is False
    assert diagnosis.lease_ever_created is False
    assert diagnosis.builder_outcome_recorded is False


# --- proves no silent claim-recovery path exists today -----------------


def test_transition_rejects_any_state_outside_the_fixed_three(tmp_path):
    """Direct proof: _transition's own validation rejects ANY target
    state that is not CLAIMED/INVOCATION_RESERVED/RESULT_RECORDED. Only
    the dedicated, evidence-gated recover_non_executed_claim function may
    ever write the terminal ABORTED_NON_EXECUTED disposition added by the
    2026-10-18 schema migration; this general-purpose transition helper
    still cannot write it, or any other unrecognized state."""
    repo = _init_repo(tmp_path / "repo")
    base_sha = _git(repo, "rev-parse", "HEAD")
    flow = tmp_path / "flow-state"
    flow.mkdir()
    (flow / "human-mode.json").write_text('{"enabled": false}', encoding="utf-8")
    (flow / "iron-god-exclusive.json").write_text(
        '{"enabled": true, "target_product": "iron-god"}', encoding="utf-8"
    )
    fake_claim = DispatchClaim(
        event_id=1, slice_id="X", accepted_sha=base_sha, token="x" * 32, state="CLAIMED"
    )
    with pytest.raises(DispatchFenceError, match="INVALID_DISPATCH_TRANSITION"):
        _transition(
            claim=fake_claim,
            claims_path=tmp_path / "claims.sqlite",
            owner_state_root=flow,
            expected="CLAIMED",
            target="OPERATOR_VOIDED",
        )


def test_sqlite_check_constraint_also_rejects_a_fifth_state(tmp_path):
    """A second, independent layer of the same guarantee: even a raw SQL
    UPDATE bypassing all Python-level validation is rejected by the
    table's own CHECK constraint -- which now allows exactly four states
    (CLAIMED, INVOCATION_RESERVED, RESULT_RECORDED, and the 2026-10-18
    terminal ABORTED_NON_EXECUTED disposition), never a fifth."""
    from project_orchestrator.local_dispatch_event_claim import _connect, _initialize

    claims_path = tmp_path / "claims.sqlite"
    db = _connect(claims_path)
    _initialize(db, claims_path)
    db.execute(
        "INSERT INTO acceptance_dispatch_claims "
        "(source_db, owner_root, acceptance_event_id, milestone_id, repository, "
        "slice_id, accepted_sha, claim_token, attempt, state, result_status, "
        "recovered_from_token, recovery_authorized_by, recovery_reason, "
        "recovery_evidence_json, claimed_utc, updated_utc) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "source",
            "root",
            1,
            "M-0",
            REPO_IDENTIFIER,
            "X",
            "a" * 40,
            "t" * 32,
            1,
            "CLAIMED",
            None,
            None,
            None,
            None,
            None,
            "2026-10-18T00:00:00+00:00",
            "2026-10-18T00:00:00+00:00",
        ),
    )
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "UPDATE acceptance_dispatch_claims SET state=? WHERE claim_token=?",
            ("OPERATOR_VOIDED", "t" * 32),
        )
        db.commit()
    db.close()


def test_retrying_the_same_claim_is_fenced_regardless_of_its_current_state(tmp_path):
    """Direct, real proof of the second blocker the owner reported:
    once a claim exists for (acceptance_event_id, slice_id), ANY
    subsequent claim_accepted_next call for the SAME milestone raises
    DispatchFenceError -- even though the first claim never progressed
    past INVOCATION_RESERVED and nothing durable (lease, build, commit)
    ever happened. Fixing the authorization boundary alone would not
    have allowed a retry here."""
    repo = _init_repo(tmp_path / "repo")
    base_sha = _git(repo, "rev-parse", "HEAD")
    flow = tmp_path / "flow-state"
    flow.mkdir()
    (flow / "human-mode.json").write_text('{"enabled": false}', encoding="utf-8")
    (flow / "iron-god-exclusive.json").write_text(
        '{"enabled": true, "target_product": "iron-god"}', encoding="utf-8"
    )
    refs = LocalGitRefs(repo, owner_state_root=flow)
    refs.bootstrap_accepted(base_sha)
    refs.protect("milestone", "M-0", base_sha)
    state = LocalFirstState(tmp_path / "accepted.sqlite")
    state.prepare_acceptance(
        repository=REPO_IDENTIFIER,
        milestone_id="M-0",
        accepted_base_sha="0" * 40,
        accepted_sha=base_sha,
        verification_sha256="a" * 64,
        product_reality="NOT_REQUIRED",
        context_json="{}",
    )
    state.finish_prepared_acceptance(
        milestone_id="M-0", expected_sha=base_sha, expected_verification_sha256="a" * 64
    )
    handoffs = {
        "PROOF-SLICE-1": {
            "schema_version": 1,
            "slice_id": "PROOF-SLICE-1",
            "state": "ELIGIBLE",
            "worktree_name": "wt-1",
            "branch": "iron-god/proof-1",
            "exact_base_sha": base_sha,
            "repository": REPO_IDENTIFIER,
            "requires_milestones": [],
        }
    }
    controller = {
        "mode": "candidate_mode",
        "candidate_execution_authorized": True,
        "slices": [{"id": "PROOF-SLICE-1", "state": "ELIGIBLE"}],
    }
    claims_path = tmp_path / "claims.sqlite"

    claim = claim_accepted_next(
        state=state,
        refs=refs,
        owner_state_root=flow,
        milestone_id="M-0",
        controller=controller,
        handoffs=handoffs,
        claims_path=claims_path,
    )
    _transition(
        claim=claim,
        claims_path=claims_path,
        owner_state_root=flow,
        expected="CLAIMED",
        target="INVOCATION_RESERVED",
    )
    # Retry for the SAME milestone -- the only slice eligible is the
    # same PROOF-SLICE-1, already claimed.
    with pytest.raises(DispatchFenceError, match="EVENT_OR_SLICE_ALREADY_CLAIMED"):
        claim_accepted_next(
            state=state,
            refs=refs,
            owner_state_root=flow,
            milestone_id="M-0",
            controller=controller,
            handoffs=handoffs,
            claims_path=claims_path,
        )
