"""Human-reviewed, missing-lease-store claim adjudication.

Owner directive (2026-10-18, "RECOVERY COMPLETION AND SCOPED
AUTHORIZATION INTEGRATION", item 1): a lease database's total absence
cannot, by itself, prove a reservation never reached a worker -- it could
equally mean lost or deleted evidence. This is a DIFFERENT, deliberately
separate path from the automatic `recover_non_executed_claim` (which
only ever recovers a lease store that was actually checked and found
empty). This path requires an explicit, attributed human attestation and
independently cross-checks every other signal it can read; any
contradiction makes the claim ineligible.
"""

from __future__ import annotations

import concurrent.futures

import pytest

from project_orchestrator.local_dispatch_event_claim import (
    DispatchFenceError,
    ReviewedNonExecutionAttestation,
    adjudicate_missing_lease_store_claim,
    inspect_claims,
    invoke_once,
    record_reviewed_non_execution_disposition,
)
from project_orchestrator.iron_god_candidate_refs import set_candidate
from test_iron_god_dispatch_claim_recovery import reserve_to_invocation_reserved
from test_local_dispatch_event_claim import accept_feature_a, demo, git, reserve, store
from test_local_dispatch_outcome_reconciliation import build_store, worker_store

REVIEWER = "jeremyweter@gmail.com"


def valid_attestation(**overrides):
    fields = dict(
        reviewer_identity=REVIEWER,
        justification=(
            "The persistent controller was still in Shadow Mode; the "
            "worker's default authorization predicate rejected the "
            "invocation before any lease could be acquired."
        ),
        authorization_rejected_before_leasing=True,
        rejection_evidence_reference="OWNER_OR_RESOURCE_VETO failure, 2026-10-18 commissioning attempt",
    )
    fields.update(overrides)
    return ReviewedNonExecutionAttestation(**fields)


def adjudicate(demo, claim, **overrides):
    args = dict(
        claim=claim,
        refs=demo[2],
        owner_state_root=demo[1],
        claims_path=store(demo),
        worker_db_path=worker_store(demo),
        repo_root=demo[0],
        attestation=valid_attestation(),
    )
    args.update(overrides)
    return adjudicate_missing_lease_store_claim(**args)


def record(demo, claim, **overrides):
    args = dict(
        claim=claim,
        refs=demo[2],
        owner_state_root=demo[1],
        claims_path=store(demo),
        worker_db_path=worker_store(demo),
        repo_root=demo[0],
        attestation=valid_attestation(),
    )
    args.update(overrides)
    return record_reviewed_non_execution_disposition(**args)


# --- eligible / baseline shape ----------------------------------------


def test_eligible_with_full_attestation_and_no_worktree_check(demo):
    claim = reserve_to_invocation_reserved(demo)
    assert not worker_store(demo).exists()
    verdict = adjudicate(demo, claim)
    assert verdict.eligible is True
    assert verdict.reasons == ()
    assert verdict.worker_store_exists is False
    assert verdict.candidate_commit_sha is None
    assert verdict.accepted_ref_matches_claim is True
    assert verdict.worktree_checked is False


def test_eligible_with_clean_matching_worktree(demo):
    claim = reserve_to_invocation_reserved(demo)
    verdict = adjudicate(demo, claim, worktree_path=demo[0])
    assert verdict.eligible is True
    assert verdict.worktree_checked is True
    assert verdict.worktree_clean is True
    assert verdict.worktree_head_matches_base is True


def test_record_applies_only_when_eligible(demo):
    claim = reserve_to_invocation_reserved(demo)
    terminal = record(demo, claim)
    assert terminal.state == "ABORTED_NON_EXECUTED"
    rows = inspect_claims(store(demo))
    assert rows[0]["state"] == "ABORTED_NON_EXECUTED"
    assert rows[0]["recovery_authorized_by"] == REVIEWER
    assert "Shadow Mode" in rows[0]["recovery_reason"]


# --- attestation completeness -------------------------------------------


def test_refuses_without_rejection_finding(demo):
    claim = reserve_to_invocation_reserved(demo)
    verdict = adjudicate(
        demo,
        claim,
        attestation=valid_attestation(authorization_rejected_before_leasing=False),
    )
    assert verdict.eligible is False
    assert "ATTESTATION_DOES_NOT_ESTABLISH_REJECTION_BEFORE_LEASING" in verdict.reasons


def test_refuses_without_reviewer_identity(demo):
    claim = reserve_to_invocation_reserved(demo)
    verdict = adjudicate(
        demo, claim, attestation=valid_attestation(reviewer_identity="   ")
    )
    assert "ATTESTATION_REVIEWER_IDENTITY_REQUIRED" in verdict.reasons


def test_refuses_without_justification(demo):
    claim = reserve_to_invocation_reserved(demo)
    verdict = adjudicate(demo, claim, attestation=valid_attestation(justification=""))
    assert "ATTESTATION_JUSTIFICATION_REQUIRED" in verdict.reasons


def test_refuses_without_evidence_reference(demo):
    claim = reserve_to_invocation_reserved(demo)
    verdict = adjudicate(
        demo, claim, attestation=valid_attestation(rejection_evidence_reference="")
    )
    assert "ATTESTATION_REJECTION_EVIDENCE_REFERENCE_REQUIRED" in verdict.reasons


def test_record_rejects_incomplete_attestation_without_mutating(demo):
    claim = reserve_to_invocation_reserved(demo)
    with pytest.raises(
        DispatchFenceError, match="REVIEWED_NON_EXECUTION_EVIDENCE_INCONCLUSIVE"
    ):
        record(demo, claim, attestation=valid_attestation(justification=""))
    assert inspect_claims(store(demo))[0]["state"] == "INVOCATION_RESERVED"


# --- independent evidence must corroborate, never just the attestation --


def test_refuses_when_lease_store_actually_exists(demo):
    """This path is specifically for a MISSING store; an existing store
    (even an empty one) must go through the automatic path instead."""
    claim = reserve_to_invocation_reserved(demo)
    build_store(demo)
    verdict = adjudicate(demo, claim)
    assert verdict.eligible is False
    assert "WORKER_STORE_EXISTS_USE_AUTOMATIC_RECOVERY_PATH" in verdict.reasons


def test_refuses_when_candidate_commit_exists(demo):
    claim = reserve_to_invocation_reserved(demo)
    set_candidate(demo[0], "IG-B", demo[5], expected_current_sha=None)
    verdict = adjudicate(demo, claim)
    assert verdict.eligible is False
    assert any("CANDIDATE_COMMIT_EXISTS" in r for r in verdict.reasons)


def test_refuses_when_worktree_is_dirty_despite_attestation(demo):
    claim = reserve_to_invocation_reserved(demo)
    (demo[0] / "stray_change.py").write_text("x = 1\n", encoding="utf-8")
    verdict = adjudicate(demo, claim, worktree_path=demo[0])
    assert verdict.eligible is False
    assert "WORKTREE_NOT_CLEAN_OR_HEAD_MISMATCH" in verdict.reasons


def test_refuses_when_worktree_head_has_moved_despite_attestation(demo):
    claim = reserve_to_invocation_reserved(demo)
    (demo[0] / "unexpected_commit.py").write_text("x = 1\n", encoding="utf-8")
    git(demo[0], "add", "unexpected_commit.py")
    git(demo[0], "commit", "-qm", "an unexpected commit happened")
    verdict = adjudicate(demo, claim, worktree_path=demo[0])
    assert verdict.eligible is False
    assert "WORKTREE_NOT_CLEAN_OR_HEAD_MISMATCH" in verdict.reasons


def test_refuses_when_accepted_ref_moved_since_the_claim(demo):
    claim = reserve_to_invocation_reserved(demo)
    refs = demo[2]
    refs.protect("recovery", "manual-check", demo[5])  # unrelated write; sanity only
    # Simulate drift by directly moving the protected accepted ref forward
    # past the claim's accepted_sha onto a brand-new commit.
    (demo[0] / "advance.py").write_text("x = 1\n", encoding="utf-8")
    git(demo[0], "add", "advance.py")
    git(demo[0], "commit", "-qm", "baseline advanced after the claim")
    new_sha = git(demo[0], "rev-parse", "HEAD")
    refs.advance_accepted(expected_sha=demo[5], new_sha=new_sha)
    verdict = adjudicate(demo, claim)
    assert verdict.eligible is False
    assert "ACCEPTED_REF_MOVED_SINCE_CLAIM" in verdict.reasons


# --- identity / staleness -------------------------------------------------


def test_refuses_for_a_result_recorded_claim(demo):
    accept_feature_a(demo)
    claim = reserve(demo)
    repo = demo[0]

    def executor(_claim):
        (repo / "feature_b.py").write_text("value = 3\n", encoding="utf-8")
        git(repo, "add", "feature_b.py")
        git(repo, "commit", "-qm", "feature B")
        return git(repo, "rev-parse", "HEAD")

    invoke_once(
        claim=claim,
        claims_path=store(demo),
        owner_state_root=demo[1],
        revalidate=lambda: True,
        executor=executor,
    )
    verdict = adjudicate(demo, claim)
    assert verdict.eligible is False
    assert "STALE_OR_MISSING_CLAIM_ROW" in verdict.reasons


def test_owner_veto_blocks_the_reviewed_path(demo):
    claim = reserve_to_invocation_reserved(demo)
    (demo[1] / "human-mode.json").write_text('{"enabled":true}', encoding="utf-8")
    with pytest.raises(DispatchFenceError, match="OWNER_CONTROL_VETO"):
        record(demo, claim)
    assert inspect_claims(store(demo))[0]["state"] == "INVOCATION_RESERVED"


# --- recovered claim behaves like any other terminal disposition --------


def test_replacement_attempt_after_reviewed_recovery_is_new_token(demo):
    claim = reserve_to_invocation_reserved(demo)
    record(demo, claim)
    new_claim = reserve(demo)
    assert new_claim.token != claim.token
    rows = {r["claim_token"]: r for r in inspect_claims(store(demo))}
    assert rows[claim.token]["state"] == "ABORTED_NON_EXECUTED"
    assert rows[claim.token]["recovery_authorized_by"] == REVIEWER
    assert rows[new_claim.token]["state"] == "CLAIMED"
    assert rows[new_claim.token]["recovered_from_token"] == claim.token
    with pytest.raises(DispatchFenceError, match="STALE_DISPATCH_CLAIM"):
        invoke_once(
            claim=claim,
            claims_path=store(demo),
            owner_state_root=demo[1],
            revalidate=lambda: True,
            executor=lambda _c: pytest.fail("the old token must never execute"),
        )


def test_concurrent_reviewed_recovery_only_one_wins(demo):
    claim = reserve_to_invocation_reserved(demo)

    def attempt(_i):
        try:
            return record(demo, claim)
        except DispatchFenceError as exc:
            return str(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, (1, 2)))
    winners = [r for r in outcomes if not isinstance(r, str)]
    losers = [r for r in outcomes if isinstance(r, str)]
    assert len(winners) == 1
    assert len(losers) == 1
    assert "STALE_DISPATCH_CLAIM" in losers[0] or "RECOVERY_RACE_LOST" in losers[0]
    assert len(inspect_claims(store(demo))) == 1


def test_crash_during_reviewed_recovery_preserves_integrity(demo, monkeypatch):
    import project_orchestrator.local_dispatch_event_claim as module

    claim = reserve_to_invocation_reserved(demo)

    real_now = module._now
    calls = {"n": 0}

    def flaky_now():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated crash mid-recovery-transaction")
        return real_now()

    monkeypatch.setattr(module, "_now", flaky_now)
    with pytest.raises(RuntimeError, match="simulated crash mid-recovery-transaction"):
        record(demo, claim)
    assert inspect_claims(store(demo))[0]["state"] == "INVOCATION_RESERVED"

    monkeypatch.setattr(module, "_now", real_now)
    terminal = record(demo, claim)
    assert terminal.state == "ABORTED_NON_EXECUTED"
    assert inspect_claims(store(demo))[0]["state"] == "ABORTED_NON_EXECUTED"
