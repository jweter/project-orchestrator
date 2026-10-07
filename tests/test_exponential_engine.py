from project_orchestrator.exponential_engine import (
    build_execution_plan,
    build_worker_envelopes,
    capacity_decision,
    extract_learning_candidate,
    process_event,
    reconcile_worker_result,
)

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
    assert decision.action == "HOLD" and decision.target == 3


def test_capacity_increases_only_on_complete_green_evidence():
    evidence = {"current_capacity": 3, "comparable_evidence_windows": 3, "all_sampled_required_verification_green": True, "merge_race_count_zero_when_measured": True, "no_measured_post_merge_correctness_hotfix": True, "no_measured_repeat_failure_regression": True, "reconciliation_conflict_rate_increase": False}
    decision = capacity_decision(evidence, POLICY)
    assert decision.action == "INCREASE" and decision.target == 4


def test_capacity_backs_off_on_measured_rework():
    evidence = {"current_capacity": 4, "comparable_evidence_windows": 3, "all_sampled_required_verification_green": True, "merge_race_count_zero_when_measured": True, "no_measured_post_merge_correctness_hotfix": False, "no_measured_repeat_failure_regression": True, "reconciliation_conflict_rate_increase": False}
    decision = capacity_decision(evidence, POLICY)
    assert decision.action == "DECREASE" and decision.target == 3


def snapshot():
    return {
        "capacity_evidence": {"current_capacity": 3, "comparable_evidence_windows": 0},
        "candidates": [
            {"id": "ke-a", "project": "knowledge-engine", "repository": "jweter/knowledge-engine-ai", "state": "P5", "dependencies_clear": True, "conflict_free": True, "product_reality_required": False, "dependency_level": 0, "downstream_unlock_count": 3, "base_sha": "a", "branch": "agent/ke-a", "task": "bounded KE slice"},
            {"id": "ke-owned", "project": "knowledge-engine", "state": "P5", "dependencies_clear": True, "conflict_free": True, "actively_owned": True},
            {"id": "rs-a", "project": "rocksmith", "repository": "jweter/rocksmith-cdlc-generator", "state": "P4", "dependencies_clear": True, "conflict_free": True, "product_reality_required": False},
            {"id": "ev-a", "project": "everward", "repository": "jweter/Project-Everward", "state": "P5", "dependencies_clear": True, "conflict_free": True, "product_reality_required": False},
            {"id": "ev-pr", "project": "everward", "state": "P5", "dependencies_clear": True, "conflict_free": True, "product_reality_required": True},
        ],
    }


def test_plan_selects_one_dependency_safe_packet_per_product():
    plan = build_execution_plan(snapshot(), POLICY)
    assert [item["id"] for item in plan["selected_work_packets"]] == ["ke-a", "rs-a", "ev-a"]
    assert plan["productive_parallel_lane_target"] == 3
    assert {item["id"] for item in plan["blocked_work_packets"]} == {"ke-owned", "ev-pr"}


def test_verified_release_blocker_wins_within_same_dependency_wave():
    state = snapshot()
    state["candidates"].append({
        "id": "ke-release", "project": "knowledge-engine", "state": "P5",
        "dependencies_clear": True, "conflict_free": True,
        "dependency_level": 0, "downstream_unlock_count": 0,
        "source_revision": "head-a", "usability_gate_source_revision": "head-a",
        "usability_gate_ref": "FIRST_WORKING_BUILD:ask", "usability_gate_verified": True,
    })
    assert build_execution_plan(state, POLICY)["selected_work_packets"][0]["id"] == "ke-release"
    state["candidates"][-1]["usability_gate_source_revision"] = "stale-head"
    assert build_execution_plan(state, POLICY)["selected_work_packets"][0]["id"] == "ke-a"


def test_event_completion_immediately_requeues_and_dispatches_ready_work():
    transition = process_event(snapshot(), POLICY, {"type": "verification_completion"})
    assert transition["action"] == "DISPATCH"
    assert len(transition["worker_envelopes"]) == 3


def test_worker_envelope_never_grants_merge_or_product_reality_authority():
    envelopes = build_worker_envelopes(build_execution_plan(snapshot(), POLICY))
    assert all(item["merge_authority"] is False for item in envelopes)
    assert all(item["product_reality_authority"] is False for item in envelopes)


def test_reconciliation_fails_closed_on_stale_default_head():
    result = {"packet_id": "ke-a", "repository": "r", "branch": "b", "base_sha": "old", "exact_head_sha": "new", "state": "PREFLIGHT_GREEN", "verification": ["preflight"], "changed_paths": []}
    assert reconcile_worker_result(result, {"default_head_sha": "different"})["state"] == "RECONCILE_REQUIRED"


def test_reconciliation_promotes_only_exact_head_preflight_green():
    result = {"packet_id": "ke-a", "repository": "r", "branch": "b", "base_sha": "base", "exact_head_sha": "head", "state": "PREFLIGHT_GREEN", "verification": ["preflight"], "changed_paths": []}
    assert reconcile_worker_result(result, {"default_head_sha": "base", "overlap_detected": False, "contract_uncertain": False})["state"] == "PROMOTION_READY"


def test_learning_candidate_requires_verified_allowed_capability():
    result = {"state": "MERGED", "verified": True, "project": "rocksmith", "repository": "r", "source_ref": "pr:1", "reusable_capability": {"type": "deterministic_validator", "name": "timing-boundary"}}
    candidate = extract_learning_candidate(result)
    assert candidate["evidence_state"] == "VERIFIED"
    assert candidate["raw_payload_persisted"] is False


def test_unverified_learning_is_not_extracted():
    assert extract_learning_candidate({"state": "MERGED", "verified": False, "reusable_capability": {"type": "regression_test"}}) is None
