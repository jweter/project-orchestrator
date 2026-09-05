from __future__ import annotations

import json
import random

import pytest

from project_orchestrator.timeseries import (
    TimeseriesError,
    aggregate_windows,
    append_window,
    build_reinvestment_snapshot,
    recent_windows,
    validate_store,
    validate_window,
    write_store,
)

REF = ["https://github.com/jweter/project-orchestrator/pull/12"]


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


def measured(value, unit="seconds", sample_count=1, evidence_refs=None):
    return {
        "status": "MEASURED",
        "value": value,
        "unit": unit,
        "sample_count": sample_count,
        "evidence_refs": evidence_refs or REF,
    }


# --- schema / validation ---------------------------------------------------


def test_valid_window_receives_stable_deterministic_id() -> None:
    first = validate_window(window())
    second = validate_window(window())
    assert first["window_id"] == second["window_id"]


def test_missing_metric_key_is_rejected() -> None:
    metrics = unknown_metrics()
    del metrics["ci_failure_count"]
    with pytest.raises(TimeseriesError, match="omits metric keys"):
        validate_window(window(metrics=metrics))


def test_unsupported_metric_key_is_rejected() -> None:
    metrics = unknown_metrics()
    metrics["made_up_metric"] = {"status": "UNKNOWN"}
    with pytest.raises(TimeseriesError, match="unsupported metric keys"):
        validate_window(window(metrics=metrics))


def test_measured_metric_requires_evidence_refs() -> None:
    metrics = unknown_metrics(
        ci_failure_count={"status": "MEASURED", "value": 1, "unit": "count", "sample_count": 1, "evidence_refs": []}
    )
    with pytest.raises(TimeseriesError, match="non-empty evidence_refs"):
        validate_window(window(metrics=metrics))


def test_measured_metric_rejects_wrong_unit() -> None:
    metrics = unknown_metrics(
        ci_failure_count={"status": "MEASURED", "value": 1, "unit": "seconds", "sample_count": 1, "evidence_refs": REF}
    )
    with pytest.raises(TimeseriesError, match="unit must be"):
        validate_window(window(metrics=metrics))


def test_measured_metric_rejects_negative_value() -> None:
    metrics = unknown_metrics(merge_cycle_seconds=measured(-5))
    with pytest.raises(TimeseriesError, match="must not be negative"):
        validate_window(window(metrics=metrics))


def test_insufficient_data_requires_reason_only() -> None:
    metrics = unknown_metrics(ci_failure_count={"status": "INSUFFICIENT_DATA", "reason": "log retention expired"})
    normalized = validate_window(window(metrics=metrics))
    assert normalized["metrics"]["ci_failure_count"]["status"] == "INSUFFICIENT_DATA"


def test_insufficient_data_without_reason_is_rejected() -> None:
    metrics = unknown_metrics(ci_failure_count={"status": "INSUFFICIENT_DATA"})
    with pytest.raises(TimeseriesError, match="must carry only 'reason'"):
        validate_window(window(metrics=metrics))


def test_insufficient_data_with_blank_reason_is_rejected() -> None:
    metrics = unknown_metrics(ci_failure_count={"status": "INSUFFICIENT_DATA", "reason": "   "})
    with pytest.raises(TimeseriesError, match="non-empty string"):
        validate_window(window(metrics=metrics))


def test_unknown_project_is_rejected() -> None:
    with pytest.raises(TimeseriesError, match="project is not in the portfolio"):
        validate_window(window(project="not-a-project"))


def test_unknown_repository_is_rejected() -> None:
    with pytest.raises(TimeseriesError, match="repository is not in the portfolio"):
        validate_window(window(repository="jweter/not-a-repo"))


def test_window_end_before_start_is_rejected() -> None:
    with pytest.raises(TimeseriesError, match="strictly after"):
        validate_window(window(window_start="2026-09-05T20:01:52Z", window_end="2026-09-05T19:56:25Z"))


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(TimeseriesError, match="timezone-aware"):
        validate_window(window(window_start="2026-09-05T19:56:25"))


def test_secret_like_notes_are_rejected() -> None:
    with pytest.raises(TimeseriesError, match="secret material"):
        validate_window(window(notes="api_key: sk-abcdefghijklmnopqrstuvwxyz"))


def test_tampered_window_id_is_rejected() -> None:
    normalized = validate_window(window())
    tampered = dict(normalized)
    tampered["window_id"] = "0" * 20
    with pytest.raises(TimeseriesError, match="does not match verified content"):
        validate_window(tampered)


def test_store_rejects_duplicate_window_ids() -> None:
    normalized = validate_window(window())
    with pytest.raises(TimeseriesError, match="duplicate window_id"):
        validate_store({"schema_version": 1, "windows": [normalized, normalized]})


def test_store_rejects_wrong_schema_version() -> None:
    with pytest.raises(TimeseriesError, match="schema_version"):
        validate_store({"schema_version": 2, "windows": []})


# --- storage -----------------------------------------------------------


def test_append_window_is_idempotent() -> None:
    store = {"schema_version": 1, "windows": []}
    once = append_window(store, window())
    twice = append_window(once, window())
    assert len(twice["windows"]) == 1


def test_append_window_accumulates_distinct_windows() -> None:
    store = {"schema_version": 1, "windows": []}
    a = append_window(store, window(repository="jweter/knowledge-engine-core"))
    b = append_window(a, window(repository="jweter/knowledge-engine-web"))
    assert len(b["windows"]) == 2


def test_write_store_round_trips(tmp_path) -> None:
    path = tmp_path / "store.json"
    store = append_window({"schema_version": 1, "windows": []}, window())
    write_store(path, store)
    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert validate_store(reloaded) == store


def test_write_store_is_byte_deterministic_on_replay(tmp_path) -> None:
    path_a = tmp_path / "a.json"
    path_b = tmp_path / "b.json"
    store = append_window({"schema_version": 1, "windows": []}, window())
    write_store(path_a, store)
    write_store(path_b, validate_store(json.loads(path_a.read_text(encoding="utf-8"))))
    assert path_a.read_text(encoding="utf-8") == path_b.read_text(encoding="utf-8")


# --- querying ------------------------------------------------------------


def test_recent_windows_orders_most_recent_first() -> None:
    older = window(window_start="2026-09-01T00:00:00Z", window_end="2026-09-01T01:00:00Z")
    newer = window(window_start="2026-09-05T00:00:00Z", window_end="2026-09-05T01:00:00Z")
    store = append_window(append_window({"schema_version": 1, "windows": []}, older), newer)
    ordered = recent_windows(store)
    assert ordered[0]["window_end"] == "2026-09-05T01:00:00Z"


def test_recent_windows_filters_by_since() -> None:
    older = window(window_start="2026-09-01T00:00:00Z", window_end="2026-09-01T01:00:00Z")
    newer = window(window_start="2026-09-05T00:00:00Z", window_end="2026-09-05T01:00:00Z")
    store = append_window(append_window({"schema_version": 1, "windows": []}, older), newer)
    selected = recent_windows(store, since="2026-09-03T00:00:00Z")
    assert len(selected) == 1
    assert selected[0]["window_end"] == "2026-09-05T01:00:00Z"


def test_recent_windows_filters_by_repository() -> None:
    core = window(repository="jweter/knowledge-engine-core")
    web = window(repository="jweter/knowledge-engine-web")
    store = append_window(append_window({"schema_version": 1, "windows": []}, core), web)
    selected = recent_windows(store, repository="jweter/knowledge-engine-web")
    assert len(selected) == 1
    assert selected[0]["repository"] == "jweter/knowledge-engine-web"


# --- aggregation ---------------------------------------------------------


def test_aggregate_computes_sample_weighted_mean() -> None:
    w1 = window(
        repository="jweter/knowledge-engine-core",
        metrics=unknown_metrics(merge_cycle_seconds=measured(100, sample_count=2)),
    )
    w2 = window(
        repository="jweter/knowledge-engine-core",
        window_start="2026-09-06T00:00:00Z",
        window_end="2026-09-06T01:00:00Z",
        metrics=unknown_metrics(merge_cycle_seconds=measured(400, sample_count=1)),
    )
    result = aggregate_windows([w1, w2], group_by="repository")
    metric = result["jweter/knowledge-engine-core"]["metrics"]["merge_cycle_seconds"]
    assert metric["status"] == "MEASURED"
    # (100*2 + 400*1) / 3 = 200
    assert metric["value"] == 200
    assert metric["sample_count"] == 3


def test_aggregate_is_order_independent() -> None:
    windows = [
        window(
            repository="jweter/knowledge-engine-core",
            window_start=f"2026-09-0{i}T00:00:00Z",
            window_end=f"2026-09-0{i}T01:00:00Z",
            metrics=unknown_metrics(ci_failure_count=measured(i, unit="count")),
        )
        for i in range(1, 6)
    ]
    shuffled = list(windows)
    random.Random(7).shuffle(shuffled)
    forward = aggregate_windows(windows, group_by="repository")
    reordered = aggregate_windows(shuffled, group_by="repository")
    assert forward == reordered


def test_aggregate_returns_unknown_when_nothing_measured() -> None:
    result = aggregate_windows([window()], group_by="portfolio")
    metric = result["portfolio"]["metrics"]["ci_failure_count"]
    assert metric["status"] == "UNKNOWN"


def test_aggregate_returns_insufficient_data_when_no_measured_but_some_insufficient() -> None:
    w = window(metrics=unknown_metrics(ci_failure_count={"status": "INSUFFICIENT_DATA", "reason": "no logs retained"}))
    result = aggregate_windows([w], group_by="portfolio")
    metric = result["portfolio"]["metrics"]["ci_failure_count"]
    assert metric["status"] == "INSUFFICIENT_DATA"


def test_aggregate_by_project_groups_across_repositories() -> None:
    w1 = window(repository="jweter/knowledge-engine-core")
    w2 = window(
        repository="jweter/knowledge-engine-web",
        window_start="2026-09-06T00:00:00Z",
        window_end="2026-09-06T01:00:00Z",
    )
    result = aggregate_windows([w1, w2], group_by="project")
    assert result["portfolio"]["window_count"] == 2


# --- reinvestment interface ------------------------------------------------


def test_reinvestment_snapshot_has_stable_shape() -> None:
    store = append_window({"schema_version": 1, "windows": []}, window())
    snapshot = build_reinvestment_snapshot(store)
    assert snapshot["schema_version"] == 1
    assert snapshot["generated_from_window_count"] == 1
    assert "portfolio" in snapshot
    assert "by_project" in snapshot
    assert "by_repository" in snapshot


def test_reinvestment_snapshot_respects_since_filter() -> None:
    older = window(window_start="2026-09-01T00:00:00Z", window_end="2026-09-01T01:00:00Z")
    store = append_window({"schema_version": 1, "windows": []}, older)
    snapshot = build_reinvestment_snapshot(store, since="2026-09-03T00:00:00Z")
    assert snapshot["generated_from_window_count"] == 0
