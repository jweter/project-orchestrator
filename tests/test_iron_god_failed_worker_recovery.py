"""Regression for recovery after failed worker packets, not owner pauses or CI waits."""
from __future__ import annotations

from project_orchestrator import iron_god_backlog as backlog
from project_orchestrator import iron_god_product_lane as lane


def item(ident, repo):
    return {"id": ident, "repository": repo, "status": "approved",
            "assignee": "iron-god", "level": 1, "depends_on": [], "interfaces": []}


def state(outcome="FAIL: SLICE_TESTS_FAILED", packet="PACKET-A"):
    return {"schema_version": 1, "items": {
        "A": {"outcome": outcome, "attempts": 1, "packets": [packet]},
    }}


def select(ledger, *, selected="repo-a", chosen=None, approved=None, busy=None, routable=None):
    chosen = chosen if chosen is not None else {"repo-a": item("A", "repo-a"),
                                                 "repo-b": item("B", "repo-b")}
    approved = approved if approved is not None else list(chosen.values())
    return lane.choose_failed_worker_alternative(
        selected, ["repo-a", "repo-b"], busy or set(), chosen,
        approved, ledger, max_level=1, routable=routable,
    )


def test_failed_worker_prefers_other_approved_repository():
    result = select(state())
    assert result is not None
    repo, selected, packet = result
    assert (repo, selected["id"], packet) == ("repo-b", "B", "PACKET-A")


def test_owner_pause_environment_and_accepted_worker_are_not_code_failures():
    for outcome in ("ENVIRONMENT_FAILURE: WAITING_FOR_MEMORY",
                    "CANCELLED", "ACCEPTED_LOCAL", "MERGED", "DISPATCHED"):
        assert select(state(outcome)) is None


def test_blocked_or_unroutable_candidate_is_never_selected():
    assert select(state(), busy={"repo-b"}) is None
    assert select(state(), routable=lambda task: False) is None


def test_fallback_can_use_another_approved_item_in_same_repo():
    a, c = item("A", "repo-a"), item("C", "repo-a")
    assert select(state(), chosen={"repo-a": a}, approved=[a, c]) == (
        "repo-a", c, "PACKET-A"
    )


def test_unapproved_same_repo_item_is_not_an_alternative():
    a, c = item("A", "repo-a"), item("C", "repo-a")
    c["status"] = "proposed"
    assert select(state(), chosen={"repo-a": a}, approved=[a, c]) is None


def test_one_durable_diversion_per_failure_without_resetting_attempts(tmp_path):
    backlog.record_item(tmp_path, "A", packet_id="PACKET-A",
                        outcome="FAIL: SLICE_TESTS_FAILED")
    before = backlog.load_state(tmp_path)["items"]["A"]
    assert before["attempts"] == 1
    assert select(backlog.load_state(tmp_path)) is not None
    assert backlog.record_recovery_diversion(
        tmp_path, "A", failed_packet_id="PACKET-A",
        alternative_packet_id="PACKET-B", alternative_item_id="B"
    )
    assert not backlog.record_recovery_diversion(
        tmp_path, "A", failed_packet_id="PACKET-A",
        alternative_packet_id="PACKET-C", alternative_item_id="C"
    )
    after = backlog.load_state(tmp_path)
    assert after["items"]["A"]["attempts"] == 1
    assert after["items"]["A"]["recovery_alternative_packet"] == "PACKET-B"
    assert select(after) is None
    # A genuinely new failed worker packet merits another *bounded* detour.
    backlog.record_item(tmp_path, "A", packet_id="PACKET-C",
                        outcome="FAIL: SLICE_TESTS_FAILED")
    assert select(backlog.load_state(tmp_path)) is not None
    assert not backlog.record_recovery_diversion(
        tmp_path, "A", failed_packet_id="PACKET-A",
        alternative_packet_id="PACKET-D", alternative_item_id="B"
    )


def test_record_does_not_invent_an_event_or_reassign_itself(tmp_path):
    assert not backlog.record_recovery_diversion(
        tmp_path, "UNKNOWN", failed_packet_id="X",
        alternative_packet_id="Y", alternative_item_id="Z"
    )
    backlog.record_item(tmp_path, "A", packet_id="X", outcome="PASS")
    assert not backlog.record_recovery_diversion(
        tmp_path, "A", failed_packet_id="X",
        alternative_packet_id="Y", alternative_item_id="B"
    )
    backlog.record_item(tmp_path, "A", packet_id="Z", outcome="FAIL: NO_PROGRESS")
    assert not backlog.record_recovery_diversion(
        tmp_path, "A", failed_packet_id="Z",
        alternative_packet_id="Z", alternative_item_id="A"
    )
