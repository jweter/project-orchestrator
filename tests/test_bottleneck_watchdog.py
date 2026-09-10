import pytest

from project_orchestrator.bottleneck_watchdog import apply_watchdog_run


def run(*lanes, observed_at="2026-09-09T20:00:00Z"):
    return {"observed_at": observed_at, "lanes": list(lanes)}


def lane(
    lane_id="rocksmith/ci",
    project="rocksmith",
    promotion_state="CI_PENDING",
    eligible=True,
    advanced=False,
    next_gate="fresh CI",
    blocker_fingerprint="CI_ACTION_REQUIRED_ZERO_JOBS",
):
    return {
        "lane_id": lane_id,
        "project": project,
        "promotion_state": promotion_state,
        "eligible": eligible,
        "advanced": advanced,
        "next_gate": next_gate,
        "blocker_fingerprint": blocker_fingerprint,
    }


def test_first_blocked_run_rotates_lane_without_parking_project():
    state = apply_watchdog_run(state=None, run=run(lane()))
    assert state["actions"][0]["kind"] == "PARKED_FOR_RETRY"
    assert state["actions"][0]["priority"] == "ROTATE"
    assert state["lanes"]["rocksmith/ci"][
        "consecutive_same_blocker_no_advancement_runs"
    ] == 1


def test_same_no_progress_fingerprint_twice_becomes_p1_orchestration_defect():
    first = apply_watchdog_run(state=None, run=run(lane()))
    second = apply_watchdog_run(
        state=first,
        run=run(lane(), observed_at="2026-09-09T21:00:00Z"),
    )
    action = second["actions"][0]
    assert action["priority"] == "P1"
    assert action["kind"] == "ORCHESTRATION_DEFECT"
    assert action["consecutive_runs"] == 2


def test_different_blocker_resets_repeat_counter():
    first = apply_watchdog_run(state=None, run=run(lane()))
    changed = lane(blocker_fingerprint="GITHUB_WRITE_REJECTED")
    second = apply_watchdog_run(
        state=first,
        run=run(changed, observed_at="2026-09-09T21:00:00Z"),
    )
    assert second["lanes"]["rocksmith/ci"][
        "consecutive_same_blocker_no_advancement_runs"
    ] == 1


def test_advancement_clears_blocker_and_records_last_advancement():
    first = apply_watchdog_run(state=None, run=run(lane()))
    progressed = lane(
        promotion_state="GREEN",
        advanced=True,
        blocker_fingerprint=None,
        next_gate="merge",
    )
    second = apply_watchdog_run(
        state=first,
        run=run(progressed, observed_at="2026-09-09T21:00:00Z"),
    )
    current = second["lanes"]["rocksmith/ci"]
    assert current["blocker_fingerprint"] is None
    assert current["last_advancement_at"] == "2026-09-09T21:00:00Z"
    assert current["consecutive_same_blocker_no_advancement_runs"] == 0


def test_promotion_transition_records_measured_flow_duration_only():
    first_lane = lane(
        lane_id="everward/slice11",
        project="everward",
        promotion_state="IMPLEMENTING",
        advanced=True,
        blocker_fingerprint=None,
        next_gate="preflight",
    )
    first = apply_watchdog_run(state=None, run=run(first_lane))
    second_lane = lane(
        lane_id="everward/slice11",
        project="everward",
        promotion_state="PREFLIGHT_GREEN",
        advanced=True,
        blocker_fingerprint=None,
        next_gate="open PR",
    )
    second = apply_watchdog_run(
        state=first,
        run=run(second_lane, observed_at="2026-09-09T20:15:00Z"),
    )
    assert second["lanes"]["everward/slice11"]["flow_durations_seconds"][
        "IMPLEMENTING->PREFLIGHT_GREEN"
    ] == 900


def test_eligible_non_advanced_lane_requires_blocker_fingerprint():
    bad = lane(blocker_fingerprint=None)
    with pytest.raises(ValueError, match="blocker_fingerprint"):
        apply_watchdog_run(state=None, run=run(bad))


def test_unknown_timestamps_are_not_fabricated():
    with pytest.raises(ValueError, match="timezone"):
        apply_watchdog_run(
            state=None,
            run=run(lane(), observed_at="2026-09-09T20:00:00"),
        )
