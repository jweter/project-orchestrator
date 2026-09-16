from __future__ import annotations

from project_orchestrator.flow_observability import (
    READY_GATES,
    build_flow_record,
    compute_flow_kpis,
    evaluate_ready_to_merge,
)

SHA = "a" * 40


def all_clear():
    return {gate: True for gate in READY_GATES}


def test_ready_is_distinct_from_green_and_requires_every_gate() -> None:
    gates = all_clear()
    gates["required_reviews_clear"] = False
    blocked = evaluate_ready_to_merge(head_sha=SHA, gates=gates, observed_at="2026-09-16T09:00:00Z")
    assert blocked["state"] == "NOT_READY"
    assert blocked["ready_to_merge_at"] is None
    assert "required_reviews_clear" in blocked["blocking_or_unknown_gates"]

    ready = evaluate_ready_to_merge(head_sha=SHA, gates=all_clear(), observed_at="2026-09-16T09:01:00Z")
    assert ready["state"] == "READY_TO_MERGE"
    assert ready["fail_closed"] is False


def test_missing_gate_is_unknown_and_fails_closed() -> None:
    gates = all_clear()
    del gates["policy_boundaries_clear"]
    result = evaluate_ready_to_merge(head_sha=SHA, gates=gates, observed_at="2026-09-16T09:00:00Z")
    assert result["state"] == "NOT_READY"
    assert result["fail_closed"] is True


def test_flow_record_preserves_unknown_events() -> None:
    record = build_flow_record(
        repository="jweter/project-orchestrator",
        pr_number=69,
        head_sha=SHA,
        events={"pr_opened_at": "2026-09-16T09:00:00Z"},
    )
    assert record["events"]["pr_opened_at"] == "2026-09-16T09:00:00Z"
    assert record["events"]["ready_to_merge_at"] is None
    assert "ready_to_merge_at" in record["unknown_events"]
    assert record["merge_actor"] == "UNKNOWN"


def test_flow_kpis_measure_promotion_latency_without_guessing() -> None:
    record = build_flow_record(
        repository="jweter/project-orchestrator",
        pr_number=69,
        head_sha=SHA,
        events={
            "implementation_started_at": "2026-09-16T08:00:00Z",
            "pr_opened_at": "2026-09-16T08:30:00Z",
            "first_ci_green_at": "2026-09-16T08:40:00Z",
            "latest_head_ci_green_at": "2026-09-16T08:40:00Z",
            "ready_to_merge_at": "2026-09-16T08:45:00Z",
            "merged_at": "2026-09-16T09:00:00Z",
        },
        merge_actor="JEREMY_MANUAL_MERGE",
    )
    result = compute_flow_kpis([record], now="2026-09-16T09:05:00Z")["metrics"]
    assert result["ready_to_merge_to_merge_seconds"]["p50"] == 900
    assert result["latest_green_to_ready_seconds"]["p50"] == 300
    assert result["pr_open_to_merge_seconds"]["p50"] == 1800
    assert result["ready_pr_queue_depth"]["value"] == 0


def test_open_ready_pr_is_visible_in_queue() -> None:
    record = build_flow_record(
        repository="jweter/project-orchestrator",
        pr_number=70,
        head_sha=SHA,
        events={"ready_to_merge_at": "2026-09-16T09:00:00Z"},
    )
    result = compute_flow_kpis([record], now="2026-09-16T09:20:00Z")["metrics"]
    assert result["ready_pr_queue_depth"]["value"] == 1
    assert result["oldest_ready_pr_age_seconds"]["value"] == 1200
    assert result["ready_to_merge_to_merge_seconds"]["status"] == "UNKNOWN"
