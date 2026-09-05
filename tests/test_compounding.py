import pytest

from project_orchestrator.compounding import (
    CompoundingError,
    add_window,
    evaluate_compounding,
    validate_series,
)


def window(identifier, *, merges, cycle, repeat, autonomy, unlocks, human, regressions=0, duration=30):
    return {
        "window_id": identifier,
        "start": f"2026-0{identifier}-01",
        "end": f"2026-0{identifier}-30",
        "duration_days": duration,
        "evidence_state": "VERIFIED",
        "metrics": {
            "verified_merges": merges,
            "median_cycle_hours": cycle,
            "repeat_failure_rate": repeat,
            "autonomous_completion_rate": autonomy,
            "dependency_unlocks": unlocks,
            "human_intervention_rate": human,
            "quality_regressions": regressions,
        },
        "evidence_refs": [f"window-{identifier}-evidence"],
    }


def test_empty_series_is_valid_and_claim_unknown() -> None:
    series = validate_series({"schema_version": 1, "windows": []})
    result = evaluate_compounding(series)
    assert result["claim_state"] == "UNKNOWN"


def test_three_improving_windows_support_current_evidence_only() -> None:
    series = {
        "schema_version": 1,
        "windows": [
            window(1, merges=10, cycle=20, repeat=.2, autonomy=.4, unlocks=1, human=.5),
            window(2, merges=15, cycle=18, repeat=.15, autonomy=.5, unlocks=2, human=.4),
            window(3, merges=22, cycle=15, repeat=.1, autonomy=.6, unlocks=3, human=.3),
        ],
    }
    result = evaluate_compounding(series)
    assert result["claim_state"] == "SUPPORTED_BY_CURRENT_EVIDENCE"
    assert result["geometric_mean_capacity_growth"] > 1
    assert "not mathematical proof" in result["claim_caveat"]


def test_quality_regression_blocks_support() -> None:
    series = {
        "schema_version": 1,
        "windows": [
            window(1, merges=10, cycle=20, repeat=.2, autonomy=.4, unlocks=1, human=.5),
            window(2, merges=15, cycle=18, repeat=.15, autonomy=.5, unlocks=2, human=.4, regressions=1),
            window(3, merges=22, cycle=15, repeat=.1, autonomy=.6, unlocks=3, human=.3),
        ],
    }
    assert evaluate_compounding(series)["claim_state"] == "NOT_SUPPORTED"


def test_mismatched_duration_is_unknown() -> None:
    series = {
        "schema_version": 1,
        "windows": [
            window(1, merges=10, cycle=20, repeat=.2, autonomy=.4, unlocks=1, human=.5),
            window(2, merges=15, cycle=18, repeat=.15, autonomy=.5, unlocks=2, human=.4),
            window(3, merges=22, cycle=15, repeat=.1, autonomy=.6, unlocks=3, human=.3, duration=14),
        ],
    }
    assert evaluate_compounding(series)["claim_state"] == "UNKNOWN"


def test_unverified_window_is_rejected() -> None:
    candidate = window(1, merges=1, cycle=1, repeat=0, autonomy=1, unlocks=0, human=0)
    candidate["evidence_state"] = "PENDING"
    with pytest.raises(CompoundingError, match="VERIFIED"):
        add_window({"schema_version": 1, "windows": []}, candidate)
