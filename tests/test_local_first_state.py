"""Fail-closed tests for Iron God's durable local-first state store."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from project_orchestrator.local_first_state import LocalFirstState, LocalStateError


REPO = "jweter/VoiceStudio"
HEAD = "a" * 40
RECEIPT = "f" * 64


@pytest.fixture
def store(tmp_path: Path) -> LocalFirstState:
    return LocalFirstState(tmp_path / "state" / "iron-god.sqlite")


def accept(store: LocalFirstState, ident: str = "s1", sha: str = HEAD) -> bool:
    return store.record_accepted_milestone(
        repository=REPO,
        milestone_id=ident,
        accepted_sha=sha,
        verification_sha256=RECEIPT,
        product_reality="PASSED",
    )


def test_database_survives_reopen_with_milestone_and_queue(store: LocalFirstState):
    assert accept(store)
    reopened = LocalFirstState(store.path)
    snapshot = reopened.snapshot()
    assert snapshot["schema_version"] == 2
    assert snapshot["milestones"][0]["accepted_sha"] == HEAD
    assert snapshot["publications"][0]["state"] == "READY"
    assert [row["event_type"] for row in snapshot["journal"]] == [
        "MILESTONE_ACCEPTED", "PUBLICATION_QUEUED"
    ]


def test_idempotent_replay_adds_no_duplicate_journal(store: LocalFirstState):
    assert accept(store)
    assert accept(store) is False
    assert len(store.snapshot()["journal"]) == 2


def test_milestone_conflicting_replay_fails_closed(store: LocalFirstState):
    assert accept(store)
    with pytest.raises(LocalStateError, match="IDENTITY_CONFLICT"):
        accept(store, sha="b" * 40)
    assert store.snapshot()["milestones"][0]["accepted_sha"] == HEAD


@pytest.mark.parametrize(
    "fields,expected",
    [
        ({"accepted_sha": "ab12"}, "INVALID_COMMIT_SHA"),
        ({"verification_sha256": "bad"}, "INVALID_VERIFICATION_HASH"),
        ({"repository": "../secret"}, "INVALID_REPOSITORY"),
        ({"product_reality": "PENDING"}, "PRODUCT_REALITY_NOT_CLEARED"),
    ],
)
def test_invalid_acceptance_fails_without_partial_writes(
    store: LocalFirstState, fields: dict, expected: str,
):
    kwargs = {
        "repository": REPO, "milestone_id": "s1",
        "accepted_sha": HEAD, "verification_sha256": RECEIPT,
        "product_reality": "PASSED",
    }
    kwargs.update(fields)
    with pytest.raises(LocalStateError, match=expected):
        store.record_accepted_milestone(**kwargs)
    assert store.snapshot()["milestones"] == []
    assert store.snapshot()["publications"] == []
    assert store.snapshot()["journal"] == []


def test_failed_push_does_not_revert_acceptance_and_more_work_can_queue(
    store: LocalFirstState,
):
    assert accept(store, "s1")
    store.transition_publication(
        milestone_id="s1", expected_state="READY", next_state="PUSHING"
    )
    store.transition_publication(
        milestone_id="s1", expected_state="PUSHING",
        next_state="DEFERRED", detail="network_unavailable"
    )
    assert accept(store, "s2", sha="b" * 40)
    snap = store.snapshot()
    assert len(snap["milestones"]) == 2
    assert [r["state"] for r in snap["publications"]] == ["DEFERRED", "READY"]
    assert any(r["event_type"] == "PUBLICATION_DEFERRED" for r in snap["journal"])


def test_one_active_publication_per_repository(store: LocalFirstState):
    accept(store, "s1")
    accept(store, "s2", sha="b" * 40)
    store.transition_publication(
        milestone_id="s1", expected_state="READY", next_state="PUSHING"
    )
    with pytest.raises(LocalStateError, match="LANE_OCCUPIED"):
        store.transition_publication(
            milestone_id="s2", expected_state="READY", next_state="PUSHING"
        )
    store.transition_publication(
        milestone_id="s1", expected_state="PUSHING", next_state="DEFERRED"
    )
    store.transition_publication(
        milestone_id="s2", expected_state="READY", next_state="PUSHING"
    )


def test_transition_stale_expected_state_rejected(store: LocalFirstState):
    accept(store)
    with pytest.raises(LocalStateError, match="STATE_CHANGED"):
        store.transition_publication(
            milestone_id="s1", expected_state="PUSHING", next_state="DEFERRED"
        )
    assert store.snapshot()["publications"][0]["state"] == "READY"


def test_disallowed_transition_rejected(store: LocalFirstState):
    accept(store)
    with pytest.raises(LocalStateError, match="INVALID_PUBLICATION_TRANSITION"):
        store.transition_publication(
            milestone_id="s1", expected_state="READY", next_state="MERGED"
        )


def test_database_unknown_schema_version_rejected(tmp_path: Path):
    path = tmp_path / "future.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(LocalStateError, match="UNSUPPORTED_SCHEMA_VERSION"):
        LocalFirstState(path)


def test_transaction_rolls_back_if_journal_insert_fails(store: LocalFirstState):
    # Inject an isolated constraint failure, so creation must atomically undo
    # both the milestone and its publication job.
    with sqlite3.connect(store.path) as db:
        db.execute(
            "CREATE TRIGGER journal_reject BEFORE INSERT ON journal "
            "BEGIN SELECT RAISE(ABORT, 'journal blocked'); END"
        )
    with pytest.raises(sqlite3.DatabaseError):
        accept(store)
    snap = store.snapshot()
    assert snap["milestones"] == []
    assert snap["publications"] == []
    assert snap["journal"] == []


def test_concurrent_initialization_is_transactional(tmp_path: Path):
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "shared.sqlite"
    with ThreadPoolExecutor(max_workers=6) as executor:
        snapshots = list(executor.map(
            lambda _: LocalFirstState(path).snapshot()["schema_version"], range(12)
        ))
    assert snapshots == [2] * 12


def test_competing_publication_claims_have_exactly_one_winner(store: LocalFirstState):
    from concurrent.futures import ThreadPoolExecutor

    accept(store, "s1")
    accept(store, "s2", sha="b" * 40)

    def claim(ident: str) -> str:
        try:
            store.transition_publication(
                milestone_id=ident, expected_state="READY", next_state="PUSHING"
            )
            return "CLAIMED"
        except LocalStateError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ("s1", "s2")))
    assert results.count("CLAIMED") == 1
    assert results.count("REPOSITORY_PUBLICATION_LANE_OCCUPIED") == 1


def test_prepared_acceptance_fences_legacy_acceptance(store: LocalFirstState):
    """A pending CAS intent must block the old direct acceptance path."""
    assert store.prepare_acceptance(
        repository=REPO,
        milestone_id="pending",
        accepted_base_sha="b" * 40,
        accepted_sha=HEAD,
        verification_sha256=RECEIPT,
        product_reality="PASSED",
        context_json="{}",
    )
    before = store.snapshot()
    with pytest.raises(LocalStateError, match="REPOSITORY_ACCEPTANCE_LANE_OCCUPIED"):
        accept(store, "legacy", sha="c" * 40)
    after = store.snapshot()
    assert after["milestones"] == before["milestones"]
    assert after["publications"] == before["publications"]
    assert after["journal"] == before["journal"]
    assert store.pending_acceptances()[0]["milestone_id"] == "pending"


def test_prepared_acceptance_replay_is_idempotent(store: LocalFirstState):
    kwargs = dict(repository=REPO, milestone_id="prepared", accepted_base_sha="b" * 40,
                  accepted_sha=HEAD, verification_sha256=RECEIPT,
                  product_reality="PASSED", context_json="{}")
    assert store.prepare_acceptance(**kwargs) is True
    assert store.prepare_acceptance(**kwargs) is False
    assert [r["event_type"] for r in store.snapshot()["journal"]] == ["ACCEPTANCE_PREPARED"]


def test_prepared_acceptance_finish_is_atomic_and_idempotent(store: LocalFirstState):
    assert store.prepare_acceptance(repository=REPO, milestone_id="prepared",
        accepted_base_sha="b" * 40, accepted_sha=HEAD,
        verification_sha256=RECEIPT, product_reality="PASSED", context_json="{}")
    assert store.finish_prepared_acceptance(milestone_id="prepared",
        expected_sha=HEAD, expected_verification_sha256=RECEIPT)
    assert not store.finish_prepared_acceptance(milestone_id="prepared",
        expected_sha=HEAD, expected_verification_sha256=RECEIPT)
    snapshot = store.snapshot()
    assert len(snapshot["milestones"]) == 1
    assert len(snapshot["publications"]) == 1
    assert [r["event_type"] for r in snapshot["journal"]] == [
        "ACCEPTANCE_PREPARED", "MILESTONE_ACCEPTED", "PUBLICATION_QUEUED"]
    assert store.pending_acceptances() == []


def test_finish_prepared_acceptance_rejects_wrong_receipt_without_mutation(store: LocalFirstState):
    store.prepare_acceptance(repository=REPO, milestone_id="prepared",
        accepted_base_sha="b" * 40, accepted_sha=HEAD,
        verification_sha256=RECEIPT, product_reality="PASSED", context_json="{}")
    before = store.snapshot()
    with pytest.raises(LocalStateError, match="ACCEPTANCE_INTENT_IDENTITY_CONFLICT"):
        store.finish_prepared_acceptance(milestone_id="prepared",
            expected_sha=HEAD, expected_verification_sha256="0" * 64)
    assert store.snapshot() == before


def test_competing_prepared_acceptance_preserves_first_intent(store: LocalFirstState):
    first = dict(repository=REPO, milestone_id="first", accepted_base_sha="b" * 40,
                 accepted_sha=HEAD, verification_sha256=RECEIPT,
                 product_reality="PASSED", context_json="{}")
    assert store.prepare_acceptance(**first)
    before = store.snapshot()
    with pytest.raises(LocalStateError, match="REPOSITORY_ACCEPTANCE_LANE_OCCUPIED"):
        store.prepare_acceptance(**{**first, "milestone_id": "second"})
    assert store.snapshot() == before


def test_prepared_acceptances_are_isolated_by_repository(store: LocalFirstState):
    first = dict(repository=REPO, milestone_id="first", accepted_base_sha="b" * 40,
                 accepted_sha=HEAD, verification_sha256=RECEIPT,
                 product_reality="PASSED", context_json="{}")
    assert store.prepare_acceptance(**first)
    assert store.prepare_acceptance(**{**first, "repository": "jweter/Everward",
                                      "milestone_id": "second"})
    assert len(store.pending_acceptances()) == 2


def test_prepared_intent_survives_process_reopen_for_recovery(store: LocalFirstState):
    """A crash after PREPARE must leave a durable recoverable intent."""
    assert store.prepare_acceptance(repository=REPO, milestone_id="crash-window",
        accepted_base_sha="b" * 40, accepted_sha=HEAD,
        verification_sha256=RECEIPT, product_reality="PASSED", context_json="{}")
    reopened = LocalFirstState(store.path)
    pending = reopened.pending_acceptances()
    assert len(pending) == 1
    assert pending[0]["milestone_id"] == "crash-window"
    assert pending[0]["accepted_base_sha"] == "b" * 40
    assert reopened.snapshot()["milestones"] == []
    assert reopened.snapshot()["publications"] == []
    assert reopened.finish_prepared_acceptance(milestone_id="crash-window",
        expected_sha=HEAD, expected_verification_sha256=RECEIPT)
    assert LocalFirstState(store.path).pending_acceptances() == []


def test_prepared_intent_replay_rejects_divergent_context(store: LocalFirstState):
    kwargs = dict(repository=REPO, milestone_id="replay", accepted_base_sha="b" * 40,
                  accepted_sha=HEAD, verification_sha256=RECEIPT,
                  product_reality="PASSED", context_json="{}")
    assert store.prepare_acceptance(**kwargs)
    before = store.snapshot()
    with pytest.raises(LocalStateError, match="ACCEPTANCE_INTENT_IDENTITY_CONFLICT"):
        store.prepare_acceptance(**{**kwargs, "context_json": '{"different":true}'})
    assert store.snapshot() == before
