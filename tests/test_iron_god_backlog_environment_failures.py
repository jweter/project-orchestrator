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
    _record(tmp_path, "p1", "ENVIRONMENT_FAILURE: X")
    _record(tmp_path, "p2", "DISPATCHED")
    row = _record(tmp_path, "p2", "FAIL: SLICE_TESTS_FAILED")
    assert row["attempts"] == 1


def test_environment_failures_are_bounded_so_a_persistent_fault_cannot_loop(tmp_path: Path) -> None:
    for n in range(backlog.MAX_ENVIRONMENT_RETRIES + 3):
        _record(tmp_path, f"p{n}", "DISPATCHED")
        row = _record(tmp_path, f"p{n}", "ENVIRONMENT_FAILURE: X")
    assert row["attempts"] == 3


def test_a_packet_that_later_succeeds_is_not_counted_as_environmental(tmp_path: Path) -> None:
    _record(tmp_path, "p1", "ENVIRONMENT_FAILURE: X")
    row = _record(tmp_path, "p1", "FAIL: SLICE_TESTS_FAILED")
    assert row["attempts"] == 1
