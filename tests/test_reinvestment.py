from __future__ import annotations

import json
import random

import pytest

from project_orchestrator.reinvestment import (
    MIN_CONFIDENCE_FOR_SELECTION,
    ReinvestmentError,
    build_plan,
    generate_candidates,
)
from project_orchestrator.timeseries import append_window, build_reinvestment_snapshot

REF12 = ["https://github.com/jweter/project-orchestrator/pull/12"]


def unknown_metrics(**overrides) -> dict:
    metrics = {
        "pr_creation_to_green_seconds": {"status": "UNKNOWN"},
        "failure_to_repair_seconds": {"status": "UNKNOWN"},
        "merge_cycle_seconds": {"status": "UNKNOWN"},
        "blocked_waiting_seconds": {"status": "UNKNOWN"},
        "ci_failure_count": {"status": "UNKNOWN"},
        "ci_repeat_failure_count": {"status": "UNKNOWN"},
        "preflight_failures_prevented_count": {"status": "UNKNOWN"},
        "roadmap_slices_completed_count": {"status": "UNKNOWN"},
        "product_reality_debt_created_count": {"status": "UNKNOWN"},
        "product_reality_debt_resolved_count": {"status": "UNKNOWN"},
        "cross_repo_incident_count": {"status": "UNKNOWN"},
        "autonomous_intervention_count": {"status": "UNKNOWN"},
        "jeremy_required_intervention_count": {"status": "UNKNOWN"},
    }
    metrics.update(overrides)
    return metrics


def window(**overrides) -> dict:
    payload = {
        "project": "portfolio",
        "repository": "jweter/project-orchestrator",
        "window_start": "2026-09-05T19:56:25Z",
        "window_end": "2026-09-05T20:01:52Z",
        "metrics": unknown_metrics(),
    }
    payload.update(overrides)
    return payload


def measured(value, unit="count", sample_count=1, evidence_refs=None):
    return {
        "status": "MEASURED",
        "value": value,
        "unit": unit,
        "sample_count": sample_count,
        "evidence_refs": evidence_refs or REF12,
    }


def snapshot_from(*windows) -> dict:
    store = {"schema_version": 1, "windows": []}
    for w in windows:
        store = append_window(store, w)
    return build_reinvestment_snapshot(store)


EMPTY_SNAPSHOT = {
    "schema_version": 1,
    "generated_from_window_count": 0,
    "since": None,
    "portfolio": {"window_count": 0, "window_ids": [], "metrics": {}},
    "by_project": {},
    "by_repository": {},
}


# --- malformed input / no evidence ----------------------------------------


def test_build_plan_rejects_non_snapshot_input() -> None:
    with pytest.raises(ReinvestmentError, match="build_reinvestment_snapshot"):
        build_plan({"not": "a snapshot"})


def test_build_plan_rejects_non_dict_input() -> None:
    with pytest.raises(ReinvestmentError):
        build_plan(["not", "a", "dict"])


def test_no_evidence_produces_only_meta_gap_candidates() -> None:
    plan = build_plan(EMPTY_SNAPSHOT)
    # with zero windows, only the two evidence-gap-shaped categories can
    # fire; no repair/delivery category has anything to point at.
    categories = {c["category"] for c in plan["candidates"]}
    assert categories <= {"orchestration_control_plane_improvement", "evidence_observability_gap"}
    assert plan["selected"] is None
    assert plan["requires_jeremy_approval"] is True
    assert any(gap["gap"] == "evidence_volume" for gap in plan["evidence_gaps"])


# --- safety policy ----------------------------------------------------


def test_safety_boundaries_are_always_present() -> None:
    plan = build_plan(EMPTY_SNAPSHOT)
    assert "no_automatic_merge_of_product_prs" in plan["safety_boundaries"]
    assert "no_bypassing_required_ci" in plan["safety_boundaries"]
    assert "no_treating_unknown_evidence_as_known" in plan["safety_boundaries"]


def test_requires_jeremy_approval_is_always_true_in_this_slice() -> None:
    rich_snapshot = snapshot_from(
        window(
            repository="jweter/knowledge-engine-core",
            metrics=unknown_metrics(
                ci_repeat_failure_count=measured(5),
                ci_failure_count=measured(8),
                pr_creation_to_green_seconds=measured(7200, unit="seconds"),
                blocked_waiting_seconds=measured(3600, unit="seconds"),
                jeremy_required_intervention_count=measured(3),
            ),
        )
    )
    plan = build_plan(rich_snapshot)
    assert plan["selected"] is not None
    assert plan["requires_jeremy_approval"] is True


# --- unknown / low-confidence evidence ---------------------------------


def test_unknown_metrics_are_omitted_from_score_not_zeroed() -> None:
    snapshot = snapshot_from(
        window(
            repository="jweter/knowledge-engine-core",
            metrics=unknown_metrics(ci_repeat_failure_count=measured(1)),
        )
    )
    candidates = generate_candidates(snapshot)
    ci_candidate = next(c for c in candidates if c["category"] == "recurring_ci_failure_prevention")
    # components backed by UNKNOWN metrics must be explicitly UNKNOWN, never 0
    assert ci_candidate["components"]["blocked_development_impact"]["status"] == "UNKNOWN"
    assert ci_candidate["components"]["human_intervention_burden"]["status"] == "UNKNOWN"
    assert ci_candidate["confidence"] < 1.0


def test_low_confidence_candidate_is_not_selected() -> None:
    # a single-window candidate with mostly UNKNOWN evidence stays below threshold
    snapshot = snapshot_from(
        window(
            repository="jweter/knowledge-engine-core",
            metrics=unknown_metrics(ci_repeat_failure_count=measured(1)),
        )
    )
    plan = build_plan(snapshot)
    candidate = next(c for c in plan["candidates"] if c["category"] == "recurring_ci_failure_prevention")
    assert candidate["confidence"] < MIN_CONFIDENCE_FOR_SELECTION
    assert plan["selected"] is None or plan["selected"]["candidate_id"] != candidate["candidate_id"]
    reasons = {r["candidate_id"]: r["reason"] for r in plan["rejected_or_deferred"]}
    assert reasons[candidate["candidate_id"]] == "confidence below minimum threshold for selection"


def test_high_confidence_candidate_is_selected() -> None:
    snapshot = snapshot_from(
        window(
            repository="jweter/knowledge-engine-core",
            metrics=unknown_metrics(
                ci_repeat_failure_count=measured(4),
                ci_failure_count=measured(6),
                pr_creation_to_green_seconds=measured(1800, unit="seconds"),
                blocked_waiting_seconds=measured(600, unit="seconds"),
                jeremy_required_intervention_count=measured(2),
            ),
        )
    )
    plan = build_plan(snapshot)
    assert plan["selected"] is not None
    assert plan["selected"]["category"] == "recurring_ci_failure_prevention"
    assert plan["selected"]["confidence"] >= MIN_CONFIDENCE_FOR_SELECTION


# --- competing candidates / ranking -------------------------------------


def test_recurring_failure_outranks_isolated_low_impact_failure() -> None:
    heavy_repeat = window(
        repository="jweter/knowledge-engine-core",
        metrics=unknown_metrics(
            ci_repeat_failure_count=measured(5),
            ci_failure_count=measured(9),
            pr_creation_to_green_seconds=measured(7200, unit="seconds"),
        ),
    )
    isolated_debt = window(
        repository="jweter/rocksmith-cdlc-generator",
        window_start="2026-09-06T00:00:00Z",
        window_end="2026-09-06T01:00:00Z",
        metrics=unknown_metrics(
            product_reality_debt_created_count=measured(1),
            product_reality_debt_resolved_count=measured(0),
        ),
    )
    snapshot = snapshot_from(heavy_repeat, isolated_debt)
    plan = build_plan(snapshot)
    top = plan["candidates"][0]
    assert top["category"] == "recurring_ci_failure_prevention"
    assert top["scope"] == "jweter/knowledge-engine-core"


def test_candidates_from_multiple_repositories_all_present() -> None:
    core_repeat = window(
        repository="jweter/knowledge-engine-core",
        metrics=unknown_metrics(ci_repeat_failure_count=measured(2)),
    )
    web_repeat = window(
        repository="jweter/knowledge-engine-web",
        window_start="2026-09-06T00:00:00Z",
        window_end="2026-09-06T01:00:00Z",
        metrics=unknown_metrics(ci_repeat_failure_count=measured(2)),
    )
    snapshot = snapshot_from(core_repeat, web_repeat)
    plan = build_plan(snapshot)
    scopes = {c["scope"] for c in plan["candidates"] if c["category"] == "recurring_ci_failure_prevention"}
    assert "jweter/knowledge-engine-core" in scopes
    assert "jweter/knowledge-engine-web" in scopes
    # two repositories triggering the same category also produces a cross-cutting portfolio rollup
    assert "portfolio" in scopes


# --- specific category triggers -----------------------------------------


def test_high_product_reality_debt_generates_scaled_candidate() -> None:
    low = snapshot_from(
        window(
            repository="jweter/rocksmith-cdlc-generator",
            metrics=unknown_metrics(
                product_reality_debt_created_count=measured(1),
                product_reality_debt_resolved_count=measured(0),
            ),
        )
    )
    high = snapshot_from(
        window(
            repository="jweter/rocksmith-cdlc-generator",
            metrics=unknown_metrics(
                product_reality_debt_created_count=measured(5),
                product_reality_debt_resolved_count=measured(0),
            ),
        )
    )
    low_candidate = next(
        c for c in generate_candidates(low) if c["category"] == "product_reality_debt_reduction"
    )
    high_candidate = next(
        c for c in generate_candidates(high) if c["category"] == "product_reality_debt_reduction"
    )
    assert high_candidate["priority_score"] > low_candidate["priority_score"]


def test_product_reality_debt_candidate_absent_when_fully_resolved() -> None:
    snapshot = snapshot_from(
        window(
            repository="jweter/rocksmith-cdlc-generator",
            metrics=unknown_metrics(
                product_reality_debt_created_count=measured(3),
                product_reality_debt_resolved_count=measured(3),
            ),
        )
    )
    candidates = generate_candidates(snapshot)
    assert not any(c["category"] == "product_reality_debt_reduction" for c in candidates)


def test_high_human_intervention_burden_generates_candidate() -> None:
    snapshot = snapshot_from(
        window(metrics=unknown_metrics(jeremy_required_intervention_count=measured(3)))
    )
    candidates = generate_candidates(snapshot)
    candidate = next(c for c in candidates if c["category"] == "jeremy_required_intervention_reduction")
    assert candidate["components"]["human_intervention_burden"]["status"] == "MEASURED"
    assert candidate["priority_score"] > 0


def test_preflight_tooling_gap_detected_when_failures_uncaught() -> None:
    snapshot = snapshot_from(
        window(
            repository="jweter/knowledge-engine-ai",
            metrics=unknown_metrics(
                preflight_failures_prevented_count=measured(0),
                ci_failure_count=measured(3),
            ),
        )
    )
    candidates = generate_candidates(snapshot)
    assert any(c["category"] == "preflight_tooling_improvement" for c in candidates)


def test_preflight_tooling_gap_absent_when_preflight_catches_failures() -> None:
    snapshot = snapshot_from(
        window(
            repository="jweter/knowledge-engine-ai",
            metrics=unknown_metrics(
                preflight_failures_prevented_count=measured(3),
                ci_failure_count=measured(3),
            ),
        )
    )
    candidates = generate_candidates(snapshot)
    assert not any(c["category"] == "preflight_tooling_improvement" for c in candidates)


def test_failure_repair_candidate_from_measured_repair_time() -> None:
    snapshot = snapshot_from(
        window(
            repository="jweter/Project-Everward",
            metrics=unknown_metrics(failure_to_repair_seconds=measured(5400, unit="seconds")),
        )
    )
    candidates = generate_candidates(snapshot)
    assert any(c["category"] == "failure_repair_automation" for c in candidates)


def test_cross_repo_candidate_from_project_scope() -> None:
    snapshot = snapshot_from(
        window(
            project="knowledge-engine",
            repository="jweter/knowledge-engine-web",
            metrics=unknown_metrics(cross_repo_incident_count=measured(2)),
        )
    )
    candidates = generate_candidates(snapshot)
    candidate = next(c for c in candidates if c["category"] == "cross_repo_compatibility_improvement")
    assert candidate["scope_type"] == "project"
    assert candidate["scope"] == "knowledge-engine"


def test_orchestration_control_plane_candidate_when_window_count_low() -> None:
    snapshot = snapshot_from(window())
    candidates = generate_candidates(snapshot)
    assert any(c["category"] == "orchestration_control_plane_improvement" for c in candidates)


def test_orchestration_control_plane_candidate_absent_with_enough_windows() -> None:
    windows = [
        window(
            window_start=f"2026-09-0{i}T00:00:00Z",
            window_end=f"2026-09-0{i}T01:00:00Z",
        )
        for i in range(1, 6)
    ]
    snapshot = snapshot_from(*windows)
    candidates = generate_candidates(snapshot)
    assert not any(c["category"] == "orchestration_control_plane_improvement" for c in candidates)


def test_evidence_observability_gap_lists_unmeasured_metrics() -> None:
    snapshot = snapshot_from(window())
    candidates = generate_candidates(snapshot)
    gap_candidate = next(c for c in candidates if c["category"] == "evidence_observability_gap")
    assert "ci_failure_count" in gap_candidate["reasons"][0]


def test_roadmap_capability_candidate_present_when_slices_measured() -> None:
    snapshot = snapshot_from(
        window(metrics=unknown_metrics(roadmap_slices_completed_count=measured(1)))
    )
    candidates = generate_candidates(snapshot)
    assert any(c["category"] == "roadmap_capability_delivery" for c in candidates)


# --- determinism ---------------------------------------------------------


def test_deterministic_replay_produces_identical_plan() -> None:
    snapshot = snapshot_from(
        window(
            repository="jweter/knowledge-engine-core",
            metrics=unknown_metrics(ci_repeat_failure_count=measured(3), ci_failure_count=measured(4)),
        )
    )
    first = build_plan(snapshot)
    second = build_plan(snapshot)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_ordering_independence_of_input_windows() -> None:
    windows = [
        window(
            repository="jweter/knowledge-engine-core",
            metrics=unknown_metrics(ci_repeat_failure_count=measured(2)),
        ),
        window(
            repository="jweter/knowledge-engine-web",
            window_start="2026-09-06T00:00:00Z",
            window_end="2026-09-06T01:00:00Z",
            metrics=unknown_metrics(failure_to_repair_seconds=measured(1200, unit="seconds")),
        ),
        window(
            repository="jweter/rocksmith-cdlc-generator",
            window_start="2026-09-07T00:00:00Z",
            window_end="2026-09-07T01:00:00Z",
            metrics=unknown_metrics(product_reality_debt_created_count=measured(1)),
        ),
    ]
    forward_store = {"schema_version": 1, "windows": []}
    for w in windows:
        forward_store = append_window(forward_store, w)
    forward_plan = build_plan(build_reinvestment_snapshot(forward_store))

    shuffled = list(windows)
    random.Random(11).shuffle(shuffled)
    reordered_store = {"schema_version": 1, "windows": []}
    for w in shuffled:
        reordered_store = append_window(reordered_store, w)
    reordered_plan = build_plan(build_reinvestment_snapshot(reordered_store))

    assert json.dumps(forward_plan, sort_keys=True) == json.dumps(reordered_plan, sort_keys=True)


def test_candidate_id_is_stable_for_identical_evidence() -> None:
    snapshot = snapshot_from(
        window(
            repository="jweter/knowledge-engine-core",
            metrics=unknown_metrics(ci_repeat_failure_count=measured(1)),
        )
    )
    first = generate_candidates(snapshot)
    second = generate_candidates(snapshot)
    assert [c["candidate_id"] for c in first] == [c["candidate_id"] for c in second]
