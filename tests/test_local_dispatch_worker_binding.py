"""Additive claim-token provenance within the preexisting local worker lease DB.

Fixtures are disposable real Git/SQLite; no production worker is launched.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import replace

import pytest

from project_orchestrator.local_dispatch_event_claim import invoke_once
from project_orchestrator.local_dispatch_worker_binding import (
    WorkerBindingError,
    bind_worker_lease,
)
from test_local_dispatch_outcome_reconciliation import (
    build_store,
    demo,
    inspect,
    put_lease,
    put_outcome,
    ready,
    store,
    worker_store,
)
from tests.owner_authority import grant_owner_authority, revoke_owner_authority

ATTEMPT = "a" * 32


def active_lease(demo, *, attempt=ATTEMPT, generation=1, ttl=600):
    db = sqlite3.connect(worker_store(demo))
    try:
        db.execute(
            "INSERT INTO slice_leases VALUES(?,?,?,?,?,?,?,?)",
            (
                "IG-B",
                attempt,
                generation,
                1001,
                demo[5],
                "BUILDING",
                time.time() + ttl,
                time.time(),
            ),
        )
        db.commit()
    finally:
        db.close()


def bind(demo, claim, *, attempt=ATTEMPT, generation=1):
    return bind_worker_lease(
        claim=claim,
        claims_path=store(demo),
        state=demo[3],
        refs=demo[2],
        owner_state_root=demo[1],
        worker_db_path=worker_store(demo),
        attempt_id=attempt,
        generation=generation,
    )


def invoke_binding(demo, claim, callback):
    return invoke_once(
        claim=claim,
        claims_path=store(demo),
        owner_state_root=demo[1],
        revalidate=lambda: True,
        executor=callback,
    )


def test_real_accepted_a_binds_worker_b_exact_token_with_no_acceptance(demo):
    claim = ready(demo)
    build_store(demo)

    def executor(_):
        put_lease(demo, attempt=ATTEMPT, when=time.time())
        active_lease(demo)
        first = bind(demo, claim)
        second = bind(demo, claim)
        assert first == second
        return first.attempt_id

    assert invoke_binding(demo, claim, executor) == ATTEMPT
    put_outcome(demo, attempt=ATTEMPT)
    result = inspect(demo)[0]
    assert result.disposition == "WORKER_OUTCOME_BOUND_NEEDS_INDEPENDENT_GIT_PROOF"
    assert result.worker_attempt_id == ATTEMPT
    assert result.candidate_sha == "c" * 40
    assert demo[2].accepted() == demo[5]
    # Original worker schema remains at v4; this is an additive table.
    db = sqlite3.connect(worker_store(demo))
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        row = db.execute(
            "SELECT claim_token,acceptance_event_id,attempt_id,generation "
            "FROM dispatch_attempt_bindings"
        ).fetchone()
    finally:
        db.close()
    assert row == (claim.token, claim.event_id, ATTEMPT, 1)


def test_claim_not_reserved_cannot_bind_even_with_matching_lease(demo):
    claim = ready(demo)
    build_store(demo)
    put_lease(demo, attempt=ATTEMPT, when=time.time())
    active_lease(demo)
    with pytest.raises(WorkerBindingError, match="DISPATCH_CLAIM_IDENTITY_UNVERIFIED"):
        bind(demo, claim)


def test_uncorrelated_worker_attempt_fails_without_linking(demo):
    claim = ready(demo)
    build_store(demo)

    def executor(_):
        put_lease(demo, attempt=ATTEMPT, when=time.time())
        active_lease(demo)
        with pytest.raises(WorkerBindingError, match="ACTIVE_WORKER_LEASE_UNVERIFIED"):
            bind(demo, claim, attempt="b" * 32)
        return None

    invoke_binding(demo, claim, executor)
    put_outcome(demo, attempt=ATTEMPT)
    assert inspect(demo)[0].disposition == (
        "WORKER_OUTCOME_UNBOUND_NEEDS_INDEPENDENT_GIT_PROOF"
    )


def test_stale_lease_cannot_bind(demo):
    claim = ready(demo)
    build_store(demo)

    def executor(_):
        put_lease(demo, attempt=ATTEMPT, when=time.time())
        active_lease(demo, ttl=-10)
        with pytest.raises(WorkerBindingError, match="ACTIVE_WORKER_LEASE_UNVERIFIED"):
            bind(demo, claim)

    invoke_binding(demo, claim, executor)


def test_owner_takeover_blocks_provenance_binding(demo):
    claim = ready(demo)
    build_store(demo)

    def executor(_):
        put_lease(demo, attempt=ATTEMPT, when=time.time())
        active_lease(demo)
        revoke_owner_authority(demo[1])
        with pytest.raises(WorkerBindingError, match="OWNER_CONTROL_VETO"):
            bind(demo, claim)

    with pytest.raises(RuntimeError, match="OWNER_CONTROL_VETO"):
        invoke_binding(demo, claim, executor)


def test_unrelated_authorized_owner_root_not_accepted(demo, tmp_path):
    claim = ready(demo)
    build_store(demo)

    def executor(_):
        put_lease(demo, attempt=ATTEMPT, when=time.time())
        active_lease(demo)
        fake = tmp_path / "fake-flow-state"
        fake.mkdir()
        grant_owner_authority(fake)
        with pytest.raises(WorkerBindingError, match="OWNER_ROOT_MISMATCH"):
            bind_worker_lease(
                claim=claim,
                claims_path=store(demo),
                state=demo[3],
                refs=demo[2],
                owner_state_root=fake,
                worker_db_path=worker_store(demo),
                attempt_id=ATTEMPT,
                generation=1,
            )

    invoke_binding(demo, claim, executor)


def test_worker_cannot_attach_token_after_outcome_is_recorded(demo):
    claim = ready(demo)
    build_store(demo)

    def executor(_):
        put_lease(demo, attempt=ATTEMPT, when=time.time())
        active_lease(demo)
        put_outcome(demo, attempt=ATTEMPT)
        with pytest.raises(WorkerBindingError, match="WORKER_OUTCOME_ALREADY_RECORDED"):
            bind(demo, claim)

    invoke_binding(demo, claim, executor)
    assert inspect(demo)[0].disposition == (
        "WORKER_OUTCOME_UNBOUND_NEEDS_INDEPENDENT_GIT_PROOF"
    )


def test_worker_db_version_is_not_downgraded_or_migrated(demo):
    claim = ready(demo)
    build_store(demo, version=901)

    def executor(_):
        with pytest.raises(WorkerBindingError, match="WORKER_SCHEMA_UNSUPPORTED"):
            bind(demo, claim)

    invoke_binding(demo, claim, executor)
    db = sqlite3.connect(worker_store(demo))
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 901
    finally:
        db.close()


def test_wrong_token_conflicts_in_readonly_reconciler(demo):
    claim = ready(demo)
    build_store(demo)

    def executor(_):
        put_lease(demo, attempt=ATTEMPT, when=time.time())
        active_lease(demo)
        bind(demo, claim)

    invoke_binding(demo, claim, executor)
    put_outcome(demo, attempt=ATTEMPT)
    db = sqlite3.connect(worker_store(demo))
    try:
        db.execute(
            "UPDATE dispatch_attempt_bindings SET claim_token=?",
            ("f" * 32,),
        )
        db.commit()
    finally:
        db.close()
    assert inspect(demo)[0].disposition == "WORKER_CLAIM_BINDING_CONFLICT"


def test_forged_candidate_sha_remains_unproven_even_with_valid_token(demo):
    claim = ready(demo)
    build_store(demo)

    def executor(_):
        put_lease(demo, attempt=ATTEMPT, when=time.time())
        active_lease(demo)
        bind(demo, claim)

    invoke_binding(demo, claim, executor)
    put_outcome(demo, attempt=ATTEMPT, digest="f" * 64)
    result = inspect(demo)[0]
    assert result.disposition == "WORKER_OUTCOME_BOUND_NEEDS_INDEPENDENT_GIT_PROOF"
    # Matching a token + a self-reported candidate does not prove Git objects.
    assert demo[2].accepted() == demo[5]


def test_preexisting_worker_lease_before_claim_cannot_be_bound(demo):
    claim = ready(demo)
    build_store(demo)

    def executor(_):
        put_lease(demo, attempt=ATTEMPT, when=1)
        active_lease(demo)
        with pytest.raises(WorkerBindingError, match="WORKER_LEASE_EVENT_CONFLICT"):
            bind(demo, claim)

    invoke_binding(demo, claim, executor)


def test_replayed_or_conflicting_dispatch_token_is_not_rebound(demo):
    claim = ready(demo)
    build_store(demo)

    def executor(_):
        put_lease(demo, attempt=ATTEMPT, when=time.time())
        active_lease(demo)
        result = bind(demo, claim)
        assert result.claim_token == claim.token
        fake = replace(claim, token="e" * 32)
        with pytest.raises(WorkerBindingError, match="DISPATCH_CLAIM_MISSING"):
            bind(demo, fake)

    invoke_binding(demo, claim, executor)
