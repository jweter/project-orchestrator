"""Durable event claim and crash/duplicate canary over real local Git and SQLite.

Imports the existing acceptance fixture (real Git commits + accepted CAS +
COMMITTED SQLite journal), never touches production refs or owner marker.
"""

from __future__ import annotations

import concurrent.futures
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from project_orchestrator.local_dispatch_event_claim import (
    DispatchFenceError,
    claim_accepted_next,
    inspect_claims,
    invoke_once,
)
from test_local_acceptance_refill import accept_feature_a, demo, git, plan


def reserve(demo, **overrides):
    _, owner, refs, state, _, _, _, _, controller, packet = demo
    args = dict(
        state=state,
        refs=refs,
        owner_state_root=owner,
        milestone_id="feature-A",
        controller=controller,
        handoffs={"IG-B": packet},
        claims_path=state.path.parent / "dispatch-claims.sqlite",
    )
    args.update(overrides)
    return claim_accepted_next(**args)


def store(demo) -> Path:
    return demo[3].path.parent / "dispatch-claims.sqlite"


def test_real_accepted_a_invokes_b_exactly_once_and_survives_restart(demo):
    assert plan(demo).status == "HELD"
    accept_feature_a(demo)
    claim = reserve(demo)
    assert claim.event_id == plan(demo).acceptance_event_id
    assert claim.slice_id == "IG-B"
    assert claim.accepted_sha == demo[5]
    assert inspect_claims(store(demo))[0]["state"] == "CLAIMED"
    with pytest.raises(DispatchFenceError, match="ALREADY_CLAIMED"):
        reserve(demo)
    invoked = []

    def executor(observed_claim):
        invoked.append(observed_claim.token)
        repo = demo[0]
        (repo / "feature_b.py").write_text("value = 3\n", encoding="utf-8")
        git(repo, "add", "feature_b.py")
        git(repo, "commit", "-qm", "feature B after accepted feature A")
        return git(repo, "rev-parse", "HEAD")

    b_commit = invoke_once(
        claim=claim,
        claims_path=store(demo),
        owner_state_root=demo[1],
        revalidate=lambda: plan(demo).status == "READY_FOR_LEASED_DISPATCH",
        executor=executor,
    )
    assert b_commit != demo[5]
    assert git(demo[0], "log", "-1", "--format=%s") == (
        "feature B after accepted feature A"
    )
    assert invoked == [claim.token]
    records = inspect_claims(store(demo))
    assert len(records) == 1
    assert records[0]["state"] == "RESULT_RECORDED"
    assert records[0]["result_status"] == "EXECUTOR_RETURNED_UNVERIFIED"
    with pytest.raises(DispatchFenceError, match="STALE_DISPATCH_CLAIM"):
        invoke_once(
            claim=claim,
            claims_path=store(demo),
            owner_state_root=demo[1],
            revalidate=lambda: True,
            executor=executor,
        )
    assert invoked == [claim.token]


def test_concurrent_contenders_only_one_event_claims(demo):
    accept_feature_a(demo)

    def attempt(_i):
        try:
            return reserve(demo)
        except DispatchFenceError as exc:
            return str(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, (1, 2)))
    winners = [r for r in outcomes if not isinstance(r, str)]
    blocked = [r for r in outcomes if isinstance(r, str)]
    assert len(winners) == 1
    assert blocked == ["EVENT_OR_SLICE_ALREADY_CLAIMED"]
    assert len(inspect_claims(store(demo))) == 1


def test_claimed_but_not_started_is_durably_visible_not_auto_replayed(demo):
    accept_feature_a(demo)
    original = reserve(demo)
    rows = inspect_claims(store(demo))
    assert rows[0]["state"] == "CLAIMED"
    assert rows[0]["claim_token"] == original.token
    with pytest.raises(DispatchFenceError, match="ALREADY_CLAIMED"):
        reserve(demo)


def test_crash_after_invocation_reservation_never_calls_worker_twice(demo):
    accept_feature_a(demo)
    claim = reserve(demo)
    calls = []

    def interrupted(_claim):
        calls.append(1)
        raise RuntimeError("worker transport lost after dispatch")

    with pytest.raises(RuntimeError, match="transport lost"):
        invoke_once(
            claim=claim,
            claims_path=store(demo),
            owner_state_root=demo[1],
            revalidate=lambda: True,
            executor=interrupted,
        )
    assert inspect_claims(store(demo))[0]["state"] == "INVOCATION_RESERVED"
    with pytest.raises(DispatchFenceError, match="STALE_DISPATCH_CLAIM"):
        invoke_once(
            claim=claim,
            claims_path=store(demo),
            owner_state_root=demo[1],
            revalidate=lambda: True,
            executor=interrupted,
        )
    assert calls == [1]


def test_owner_takeover_before_claim_denies_without_creating_store(demo):
    accept_feature_a(demo)
    (demo[1] / "human-mode.json").write_text('{"enabled":true}')
    with pytest.raises(DispatchFenceError, match="OWNER_CONTROL_VETO"):
        reserve(demo)
    assert not store(demo).exists()


def test_owner_takeover_after_claim_prevents_invocation(demo):
    accept_feature_a(demo)
    claim = reserve(demo)
    (demo[1] / "human-mode.json").write_text('{"enabled":true}')
    calls = []
    with pytest.raises(DispatchFenceError, match="OWNER_CONTROL_VETO"):
        invoke_once(
            claim=claim,
            claims_path=store(demo),
            owner_state_root=demo[1],
            revalidate=lambda: True,
            executor=lambda _: calls.append(1),
        )
    assert calls == []
    assert inspect_claims(store(demo))[0]["state"] == "CLAIMED"


def test_owner_takeover_during_worker_keeps_ambiguous_state(demo):
    accept_feature_a(demo)
    claim = reserve(demo)

    def take_over(_):
        (demo[1] / "human-mode.json").write_text('{"enabled":true}')

    with pytest.raises(DispatchFenceError, match="OWNER_CONTROL_VETO"):
        invoke_once(
            claim=claim,
            claims_path=store(demo),
            owner_state_root=demo[1],
            revalidate=lambda: True,
            executor=take_over,
        )
    assert inspect_claims(store(demo))[0]["state"] == "INVOCATION_RESERVED"


def test_revocation_between_claim_and_callback_keeps_hold(demo):
    accept_feature_a(demo)
    claim = reserve(demo)

    def revoke_during_revalidate():
        (demo[1] / "human-mode.json").write_text('{"enabled":true}')
        return True

    with pytest.raises(DispatchFenceError, match="OWNER_CONTROL_VETO"):
        invoke_once(
            claim=claim,
            claims_path=store(demo),
            owner_state_root=demo[1],
            revalidate=revoke_during_revalidate,
            executor=lambda _: pytest.fail("unauthorized launch"),
        )


@pytest.mark.parametrize(
    "controller_delta",
    [
        {"mode": "shadow_state_only"},
        {"candidate_execution_authorized": False},
    ],
)
def test_production_modes_cannot_claim_dispatch(demo, controller_delta):
    accept_feature_a(demo)
    policy = dict(demo[8], **controller_delta)
    with pytest.raises(DispatchFenceError, match="SELECTOR_NOT_READY"):
        reserve(demo, controller=policy)
    assert not store(demo).exists()


def test_uncommitted_acceptance_cannot_claim(demo):
    with pytest.raises(DispatchFenceError, match="SELECTOR_NOT_READY"):
        reserve(demo)
    assert not store(demo).exists()


def test_active_slice_cannot_be_claimed_as_new_work(demo):
    accept_feature_a(demo)
    with pytest.raises(DispatchFenceError, match="SELECTOR_NOT_READY"):
        reserve(demo, active_slice_ids=("IG-B",))


def test_dispatch_db_must_not_be_acceptance_db(demo):
    accept_feature_a(demo)
    with pytest.raises(DispatchFenceError, match="DISPATCH_DB_MUST_BE_SEPARATE"):
        reserve(demo, claims_path=demo[3].path)


def test_invalid_claim_token_does_not_invoke_builder(demo):
    accept_feature_a(demo)
    claim = reserve(demo)
    calls = []
    with pytest.raises(DispatchFenceError, match="STALE_DISPATCH_CLAIM"):
        invoke_once(
            claim=replace(claim, token="wrong-token"),
            claims_path=store(demo),
            owner_state_root=demo[1],
            revalidate=lambda: True,
            executor=lambda _: calls.append(1),
        )
    assert calls == []
    assert inspect_claims(store(demo))[0]["state"] == "CLAIMED"


def test_unrecognized_schema_does_not_drop_previous_data(demo):
    accept_feature_a(demo)
    reserve(demo)
    db = sqlite3.connect(store(demo))
    try:
        db.execute("PRAGMA user_version=901")
        db.commit()
    finally:
        db.close()
    with pytest.raises(DispatchFenceError, match="SCHEMA_UNSUPPORTED"):
        reserve(demo)
    assert len(inspect_claims(store(demo))) == 1


def test_claim_cannot_be_invoked_under_unrelated_authorized_owner_root(demo, tmp_path):
    """Stored owner authority must be bound to the durable claim identity.

    Both markers read as authorized. Only the root used at admission is
    authorized to finish a previously committed claim.
    """
    accept_feature_a(demo)
    claim = reserve(demo)
    second_owner = tmp_path / "unrelated-flow-state"
    second_owner.mkdir()
    (second_owner / "human-mode.json").write_text('{"enabled":false}', encoding="utf-8")
    callbacks = []
    with pytest.raises(DispatchFenceError, match="OWNER_ROOT_MISMATCH"):
        invoke_once(
            claim=claim,
            claims_path=store(demo),
            owner_state_root=second_owner,
            revalidate=lambda: True,
            executor=lambda _: callbacks.append("UNAUTHORIZED"),
        )
    assert callbacks == []
    assert inspect_claims(store(demo))[0]["state"] == "CLAIMED"
