from project_orchestrator.daily_bottleneck_review import build_daily_bottleneck_review


def state(actions=None):
    return {
        "schema_version": 1,
        "lanes": {
            "rocksmith/ci": {
                "project": "rocksmith",
                "eligible": True,
                "blocker_fingerprint": "CI_ACTION_REQUIRED_ZERO_JOBS",
                "consecutive_same_blocker_no_advancement_runs": 2,
                "next_gate": "platform gate resolution",
                "last_advancement_at": "2026-09-09T18:00:00Z",
            },
            "everward/visual": {
                "project": "everward",
                "eligible": True,
                "blocker_fingerprint": "UNREAL_PRODUCT_REALITY",
                "consecutive_same_blocker_no_advancement_runs": 1,
                "next_gate": "human playtest",
                "last_advancement_at": None,
            },
        },
        "actions": actions
        or [
            {"lane_id": "rocksmith/ci", "priority": "P1"},
            {"lane_id": "everward/visual", "priority": "ROTATE"},
        ],
    }


def test_daily_review_selects_highest_priority_repeated_blocker():
    report = build_daily_bottleneck_review(watchdog_state=state())
    assert report["largest_blocker"]["lane_id"] == "rocksmith/ci"
    assert report["largest_blocker"]["priority"] == "P1"


def test_verified_root_cause_enables_bounded_repair():
    events = [
        {
            "lane_id": "rocksmith/ci",
            "blocker_fingerprint": "CI_ACTION_REQUIRED_ZERO_JOBS",
            "evidence_state": "VERIFIED",
            "root_cause": "pre-job GitHub Actions gate",
            "evidence_ref": "run:34424780074",
        }
    ]
    report = build_daily_bottleneck_review(
        watchdog_state=state(), observed_events=events
    )
    assert report["largest_blocker"]["root_cause"]["status"] == "ESTABLISHED"
    assert report["remediation"]["mode"] == "REPAIR"


def test_unknown_root_cause_fails_closed_and_rotates():
    report = build_daily_bottleneck_review(watchdog_state=state())
    assert report["largest_blocker"]["root_cause"]["status"] == "UNKNOWN"
    assert report["remediation"]["mode"] == "EVIDENCE_THEN_ROTATE"


def test_no_blocker_continues_product_execution():
    empty = {"schema_version": 1, "lanes": {}, "actions": []}
    report = build_daily_bottleneck_review(watchdog_state=empty)
    assert report["largest_blocker"] is None
    assert report["remediation"]["mode"] == "NO_ACTIVE_BLOCKER"
