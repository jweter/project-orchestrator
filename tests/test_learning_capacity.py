from project_orchestrator.learning_capacity import adaptive_capacity_feedback, close_learning_loop


def policy():
    return {"capacity_controller": {
        "initial_max_concurrent_product_lanes": 3,
        "min_concurrent_product_lanes": 1,
        "max_concurrent_product_lanes": 6,
        "increase_by": 1,
        "decrease_by": 1,
        "minimum_evidence_windows_before_change": 3,
    }}


def empty_memory():
    return {"schema_version": 1, "entries": []}


def green_window():
    return {
        "comparable": True,
        "required_verification_green": True,
        "merge_race_count": 0,
        "post_merge_correctness_hotfix": False,
        "repeat_failure_regression": False,
        "reconciliation_conflict_rate_increase": False,
    }


def test_capacity_holds_until_minimum_evidence():
    result = adaptive_capacity_feedback([green_window(), green_window()], policy(), current_capacity=3)
    assert result["decision"]["action"] == "HOLD"
    assert result["decision"]["reason"] == "INSUFFICIENT_DATA"


def test_capacity_increases_after_sustained_comparable_green():
    result = adaptive_capacity_feedback([green_window(), green_window(), green_window()], policy(), current_capacity=3)
    assert result["decision"]["action"] == "INCREASE"
    assert result["decision"]["target"] == 4


def test_capacity_decreases_on_measured_rework_regression():
    bad = green_window()
    bad["post_merge_correctness_hotfix"] = True
    result = adaptive_capacity_feedback([green_window(), green_window(), bad], policy(), current_capacity=4)
    assert result["decision"]["action"] == "DECREASE"
    assert result["decision"]["target"] == 3


def test_unknown_windows_do_not_become_zero_evidence():
    result = adaptive_capacity_feedback([green_window(), {"comparable": False}, {}], policy(), current_capacity=3)
    assert result["measured_windows"] == 1
    assert result["ignored_unknown_or_noncomparable_windows"] == 2
    assert result["decision"]["action"] == "HOLD"


def test_unverified_result_does_not_extract_capability():
    result = close_learning_loop(
        {"state": "PREFLIGHT_GREEN", "verified": False, "repository": "jweter/Project-Everward"},
        empty_memory(),
        next_repository="jweter/Project-Everward",
        next_paths=["Source/Everward/Probe.cpp"],
    )
    assert result["learning_candidate"] is None
    assert result["raw_worker_payload_persisted"] is False


def test_verified_capability_is_ready_for_persistence_not_auto_authority():
    result = close_learning_loop(
        {
            "state": "MERGED",
            "verified": True,
            "project": "everward",
            "repository": "jweter/Project-Everward",
            "source_ref": "issue-270",
            "reusable_capability": {"type": "regression_test", "name": "region-delta-roundtrip"},
        },
        empty_memory(),
        next_repository="jweter/Project-Everward",
        next_paths=["Source/Everward/Probe.cpp"],
    )
    assert result["learning_extraction_state"] == "READY_FOR_VERIFIED_PERSISTENCE"
    assert result["learning_candidate"]["evidence_state"] == "VERIFIED"
    assert result["authority_preserved"] is True
