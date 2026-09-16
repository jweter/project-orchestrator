from project_orchestrator.exponential_engine import build_execution_plan, capacity_decision


POLICY = {
    "name": "Exponential Engine v1",
    "capacity_controller": {
        "initial_max_concurrent_product_lanes": 3,
        "min_concurrent_product_lanes": 1,
        "max_concurrent_product_lanes": 6,
        "increase_by": 1,
        "decrease_by": 1,
        "minimum_evidence_windows_before_change": 3,
    },
}


def test_capacity_holds_when_evidence_is_insufficient():
    decision = capacity_decision({"current_capacity": 3, "comparable_evidence_windows": 2}, POLICY)
    assert decision.action == "HOLD"
    assert decision.target == 3


def test_capacity_increases_only_on_complete_green_evidence():
    evidence = {
        "current_capacity": 3,
        "comparable_evidence_windows": 3,
        "all_sampled_required_verification_green": True,
        "merge_race_count_zero_when_measured": True,
        "no_measured_post_merge_correctness_hotfix": True,
        "no_measured_repeat_failure_regression": True,
        "reconciliation_conflict_rate_increase": False,
    }
    decision = capacity_decision(evidence, POLICY)
    assert decision.action == "INCREASE"
    assert decision.target == 4


def test_capacity_backs_off_on_measured_rework():
    evidence = {
        "current_capacity": 4,
        "comparable_evidence_windows": 3,
        "all_sampled_required_verification_green": True,
        "merge_race_count_zero_when_measured": True,
        "no_measured_post_merge_correctness_hotfix": False,
        "no_measured_repeat_failure_regression": True,
        "reconciliation_conflict_rate_increase": False,
    }
    decision = capacity_decision(evidence, POLICY)
    assert decision.action == "DECREASE"
    assert decision.target == 3


def test_plan_selects_one_dependency_safe_packet_per_product():
    snapshot = {
        "capacity_evidence": {"current_capacity": 3, "comparable_evidence_windows": 0},
        "candidates": [
            {"id": "ke-a", "project": "knowledge-engine", "state": "P5", "dependencies_clear": True, "conflict_free": True, "product_reality_required": False, "dependency_level": 0, "downstream_unlock_count": 3},
            {"id": "ke-b", "project": "knowledge-engine", "state": "P5", "dependencies_clear": True, "conflict_free": True, "product_reality_required": False, "dependency_level": 1, "downstream_unlock_count": 9},
            {"id": "rs-a", "project": "rocksmith", "state": "P4", "dependencies_clear": True, "conflict_free": True, "product_reality_required": False},
            {"id": "ev-a", "project": "everward", "state": "P5", "dependencies_clear": True, "conflict_free": True, "product_reality_required": False},
            {"id": "ev-pr", "project": "everward", "state": "P5", "dependencies_clear": True, "conflict_free": True, "product_reality_required": True},
        ],
    }
    plan = build_execution_plan(snapshot, POLICY)
    assert [item["id"] for item in plan["selected_work_packets"]] == ["ke-a", "rs-a", "ev-a"]
    assert plan["productive_parallel_lane_target"] == 3
    assert [item["id"] for item in plan["blocked_work_packets"]] == ["ev-pr"]
