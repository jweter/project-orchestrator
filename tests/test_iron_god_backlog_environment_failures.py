"""An owner takeover (ENVIRONMENT_FAILURE) must not spend an item's build attempts."""

from pathlib import Path

from project_orchestrator import iron_god_backlog as backlog


def _record(lane: Path, packet: str, outcome: str) -> dict:
    return backlog.record_item(lane, "KEC-1", packet_id=packet, outcome=outcome)


def test_environment_failures_do_not_spend_attempts(tmp_path: Path) -> None:
    for n in range(1, 6):  # five takeovers in a row
        _record(tmp_path, f"p{n}", "DISPATCHED")
        row = _record(tmp_path, f"p{n}", "ENVIRONMENT_FAILURE: EXECUTION_AUTHORITY_REVOKED_DURING_IMPLEMENTATION")
    assert row["attempts"] == 0 and len(row["packets"]) == 5  # every packet is still recorded as evidence


def test_a_real_failure_still_spends_an_attempt_and_environment_failures_do_not_hide_it(tmp_path: Path) -> None:
    _record(tmp_path, "p1", "DISPATCHED")
    _record(tmp_path, "p1", "ENVIRONMENT_FAILURE: EXECUTION_AUTHORITY_REVOKED_BEFORE_COMMIT")
    _record(tmp_path, "p2", "DISPATCHED")
    row = _record(tmp_path, "p2", "FAIL: SLICE_TESTS_FAILED")
    assert row["attempts"] == 1


def test_environment_failures_are_bounded_so_a_persistent_fault_cannot_loop(tmp_path: Path) -> None:
    for n in range(backlog.MAX_ENVIRONMENT_RETRIES + 3):
        _record(tmp_path, f"p{n}", "DISPATCHED")
        row = _record(tmp_path, f"p{n}", "ENVIRONMENT_FAILURE: EXECUTION_AUTHORITY_REVOKED_BEFORE_COMMIT")
    assert row["attempts"] == 3


def test_a_packet_that_later_succeeds_is_not_counted_as_environmental(tmp_path: Path) -> None:
    _record(tmp_path, "p1", "ENVIRONMENT_FAILURE: EXECUTION_AUTHORITY_REVOKED_BEFORE_COMMIT")
    row = _record(tmp_path, "p1", "FAIL: SLICE_TESTS_FAILED")
    assert row["attempts"] == 1


def test_other_environment_failures_still_spend_attempts(tmp_path: Path) -> None:
    _record(tmp_path, "p1", "DISPATCHED")
    row = _record(tmp_path, "p1", "ENVIRONMENT_FAILURE: TIMEOUT")
    assert row["attempts"] == 1


def test_exhausted_environment_is_not_model_exhaustion_and_other_work_remains_eligible(tmp_path):
    first = {"id": "KEC-1", "repository": "repo", "status": "approved",
             "assignee": backlog.AGENT_ID, "level": 1, "depends_on": []}
    other = {**first, "id": "KEC-2"}
    for n in range(backlog.MAX_ITEM_ATTEMPTS):
        _record(tmp_path, f"timeout-{n}", "ENVIRONMENT_FAILURE: TIMEOUT")
    state = backlog.load_state(tmp_path)
    items = [first, other]
    assert backlog.exclusion(first, state, items=items, done=set(), max_level=1)[0] == (
        "ENVIRONMENT_ATTEMPTS_EXHAUSTED")
    assert backlog.slice_state(first, state["items"]["KEC-1"], done=set(), conflicting=[]) == (
        "ENVIRONMENT_ATTEMPTS_EXHAUSTED")
    assert backlog.next_item(items, state, "repo", max_level=1) == other
    assert backlog.failed_worker_evidence(state["items"]["KEC-1"]) is None


def test_environment_interrupt_is_not_reported_ready_or_failed_implementation(tmp_path):
    row = _record(tmp_path, "p1", "ENVIRONMENT_FAILURE: TIMEOUT")
    item = {"id": "KEC-1", "status": "approved", "assignee": backlog.AGENT_ID, "depends_on": []}
    assert backlog.slice_state(item, row, done=set(), conflicting=[]) == "ENVIRONMENT_INTERRUPTED"


def test_real_exhaustion_retains_existing_classification(tmp_path):
    item = {"id": "KEC-1", "repository": "repo", "status": "approved",
            "assignee": backlog.AGENT_ID, "level": 1, "depends_on": []}
    for n in range(backlog.MAX_ITEM_ATTEMPTS):
        _record(tmp_path, f"failure-{n}", "FAIL: SLICE_TESTS_FAILED")
    state = backlog.load_state(tmp_path)
    assert backlog.exclusion(item, state, items=[item], done=set(), max_level=1)[0] == "ATTEMPTS_EXHAUSTED"
