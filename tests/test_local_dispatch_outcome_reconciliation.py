"""Read-only cross-store canary with real accepted Git refs and SQLite claims.

A worker-reported VERIFIED_CANDIDATE must never become accepted or executable
without independent exact-head Git evidence and stronger provenance binding.
"""

from __future__ import annotations

import json
import sqlite3
import time

import pytest

from project_orchestrator.local_dispatch_event_claim import invoke_once
from project_orchestrator.local_dispatch_outcome_reconciliation import (
    OutcomeBridgeError,
    reconcile_dispatch_outcomes,
)
from test_local_dispatch_event_claim import accept_feature_a, demo, reserve, store
from tests.owner_authority import grant_owner_authority, revoke_owner_authority


def worker_store(demo):
    return demo[3].path.parent / "worker-leases.sqlite"


def build_store(demo, *, version=4):
    path = worker_store(demo)
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE slice_lease_events(
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            slice_id TEXT, attempt_id TEXT, generation INTEGER,
            event_type TEXT, detail TEXT, occurred_at REAL
        );
        CREATE TABLE slice_leases(
            slice_id TEXT PRIMARY KEY, attempt_id TEXT, generation INTEGER,
            owner_pid INTEGER, base_sha TEXT, state TEXT,
            lease_until REAL, changed_at REAL
        );
        CREATE TABLE slice_outcomes(
            slice_id TEXT PRIMARY KEY, attempt_id TEXT, generation INTEGER,
            base_sha TEXT, candidate_sha TEXT, state TEXT,
            evidence_json TEXT, changed_at REAL
        );
        """
    )
    db.execute(f"PRAGMA user_version={version}")
    db.commit()
    db.close()
    return path


def put_lease(demo, *, sha=None, attempt="a1", generation=1, when=None):
    db = sqlite3.connect(worker_store(demo))
    try:
        db.execute(
            "INSERT INTO slice_lease_events "
            "(slice_id,attempt_id,generation,event_type,detail,occurred_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                "IG-B",
                attempt,
                generation,
                "SLICE_LEASED",
                demo[5] if sha is None else sha,
                (time.time() + 5) if when is None else when,
            ),
        )
        db.commit()
    finally:
        db.close()


def put_outcome(
    demo,
    *,
    attempt="a1",
    generation=1,
    base=None,
    state="VERIFIED_CANDIDATE",
    digest=None,
):
    db = sqlite3.connect(worker_store(demo))
    try:
        db.execute(
            "INSERT INTO slice_outcomes VALUES(?,?,?,?,?,?,?,?)",
            (
                "IG-B",
                attempt,
                generation,
                demo[5] if base is None else base,
                "c" * 40,
                state,
                json.dumps({"receipt_sha256": "f" * 64 if digest is None else digest}),
                time.time(),
            ),
        )
        db.commit()
    finally:
        db.close()


def inspect(demo):
    return reconcile_dispatch_outcomes(
        state=demo[3],
        refs=demo[2],
        owner_state_root=demo[1],
        claims_path=store(demo),
        worker_db_path=worker_store(demo),
    )


def ready(demo):
    accept_feature_a(demo)
    return reserve(demo)


def invoked(demo, claim):
    return invoke_once(
        claim=claim,
        claims_path=store(demo),
        owner_state_root=demo[1],
        revalidate=lambda: True,
        executor=lambda _: "no worker launched",
    )


def test_no_dispatch_claim_does_not_create_files(demo):
    assert inspect(demo) == []
    assert not store(demo).exists()
    assert not worker_store(demo).exists()


def test_claim_without_worker_db_requires_review(demo):
    claim = ready(demo)
    result = inspect(demo)
    assert result[0].event_id == claim.event_id
    assert result[0].disposition == "WORKER_STORE_MISSING"
    assert not worker_store(demo).exists()


def test_claimed_slice_with_no_lease_was_not_invoked(demo):
    ready(demo)
    build_store(demo)
    assert inspect(demo)[0].disposition == "CLAIMED_NOT_INVOKED"


def test_invocation_reserved_without_lease_is_uncertain_not_replayed(demo):
    claim = ready(demo)
    build_store(demo)
    with pytest.raises(RuntimeError, match="unknown worker outcome"):
        invoke_once(
            claim=claim,
            claims_path=store(demo),
            owner_state_root=demo[1],
            revalidate=lambda: True,
            executor=lambda _: (_ for _ in ()).throw(
                RuntimeError("unknown worker outcome")
            ),
        )
    assert inspect(demo)[0].disposition == "INVOCATION_UNCERTAIN_NO_LEASE"


def test_callback_return_without_worker_lease_does_not_prove_build(demo):
    claim = ready(demo)
    build_store(demo)
    invoked(demo, claim)
    assert inspect(demo)[0].disposition == "RETURNED_WITHOUT_WORKER_LEASE_REVIEW"


def test_matching_worker_lease_without_outcome_keeps_review(demo):
    claim = ready(demo)
    build_store(demo)
    invoked(demo, claim)
    put_lease(demo)
    record = inspect(demo)[0]
    assert record.disposition == "WORKER_OUTCOME_UNKNOWN_NO_AUTOREPLAY"
    assert record.worker_attempt_id == "a1"


def test_worker_verified_claim_is_unbound_not_accepted(demo):
    claim = ready(demo)
    build_store(demo)
    invoked(demo, claim)
    put_lease(demo)
    put_outcome(demo)
    before = inspect(demo)[0]
    assert before.disposition == "WORKER_OUTCOME_UNBOUND_NEEDS_INDEPENDENT_GIT_PROOF"
    assert before.candidate_sha == "c" * 40
    assert before.receipt_sha256 == "f" * 64
    assert demo[2].accepted() == demo[5]  # worker result NEVER advances refs
    assert len(inspect(demo)) == 1
    assert inspect(demo)[0] == before  # repeatable read-only observation


def test_worker_reported_failure_stays_in_repair_review(demo):
    claim = ready(demo)
    build_store(demo)
    invoked(demo, claim)
    put_lease(demo)
    put_outcome(demo, state="FAILED_ROUTED")
    assert inspect(demo)[0].disposition == "WORKER_FAILURE_REPAIR_REVIEW"


@pytest.mark.parametrize(
    "kind",
    [
        "bad-base",
        "earlier-than-claim",
        "multiple",
        "bad-outcome-attempt",
        "bad-outcome-digest",
    ],
)
def test_mismatches_fail_closed(demo, kind):
    claim = ready(demo)
    build_store(demo)
    invoked(demo, claim)
    if kind == "bad-base":
        put_lease(demo, sha="b" * 40)
        expected = "WORKER_LEASE_BASE_OR_TIME_CONFLICT"
    elif kind == "earlier-than-claim":
        put_lease(demo, when=1)
        expected = "WORKER_LEASE_BASE_OR_TIME_CONFLICT"
    elif kind == "multiple":
        put_lease(demo)
        put_lease(demo, attempt="a2", generation=2)
        expected = "MULTIPLE_WORKER_ATTEMPTS_REVIEW"
    elif kind == "bad-outcome-attempt":
        put_lease(demo)
        put_outcome(demo, attempt="a2")
        expected = "WORKER_OUTCOME_IDENTITY_CONFLICT"
    else:
        put_lease(demo)
        put_outcome(demo, digest="not-a-sha256")
        expected = "WORKER_EVIDENCE_UNVERIFIED"
    assert inspect(demo)[0].disposition == expected


def test_wrong_source_db_is_not_reconciled(demo):
    ready(demo)
    build_store(demo)
    db = sqlite3.connect(store(demo))
    try:
        db.execute("UPDATE acceptance_dispatch_claims SET source_db='other-db'")
        db.commit()
    finally:
        db.close()
    assert inspect(demo)[0].disposition == "CLAIM_SOURCE_PROVENANCE_CONFLICT"


def test_owner_takeover_refuses_even_read_only_advice(demo):
    ready(demo)
    build_store(demo)
    revoke_owner_authority(demo[1])
    with pytest.raises(OutcomeBridgeError, match="OWNER_CONTROL_VETO"):
        inspect(demo)


def test_accepted_ref_change_stops_reconciliation(demo):
    ready(demo)
    build_store(demo)
    (demo[0] / "next.py").write_text("x = 1\n")
    from test_local_acceptance_refill import git

    git(demo[0], "add", "next.py")
    git(demo[0], "commit", "-qm", "next accepted head")
    demo[2].advance_accepted(
        expected_sha=demo[5], new_sha=git(demo[0], "rev-parse", "HEAD")
    )
    assert inspect(demo)[0].disposition == "ACCEPTED_REF_CHANGED_HOLD"


def test_schema_drift_is_fatal_before_status_projections(demo):
    ready(demo)
    build_store(demo, version=901)
    with pytest.raises(OutcomeBridgeError, match="WORKER_SCHEMA_UNSUPPORTED"):
        inspect(demo)


def test_claimed_with_unexpected_external_lease_is_not_marked_safe(demo):
    ready(demo)
    build_store(demo)
    put_lease(demo)
    assert inspect(demo)[0].disposition == "CLAIMED_WITH_UNEXPECTED_WORKER_LEASE_REVIEW"
