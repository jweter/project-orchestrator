from __future__ import annotations

from typing import Any

from .inventory import EXPECTED_REPOSITORIES
from .throughput_guard import compute_rework_kpis
from .throughput_optimizer import MIN_WINDOWS_PER_PROJECT, optimize_throughput
from .timeseries import build_reinvestment_snapshot

PROJECT_KEYS = ("knowledge-engine", "rocksmith", "everward")

DELIVERY_METRICS: dict[str, dict[str, Any]] = {
    "pr_creation_to_green_seconds": {"unit": "seconds", "target": "trend_down"},
    "merge_cycle_seconds": {"unit": "seconds", "target": "trend_down"},
    "failure_to_repair_seconds": {"unit": "seconds", "target": "trend_down"},
    "ci_failure_count": {"unit": "count", "target": "trend_down"},
    "ci_repeat_failure_count": {"unit": "count", "target": 0},
    "blocked_waiting_seconds": {"unit": "seconds", "target": "trend_down"},
    "roadmap_slices_completed_count": {"unit": "count", "target": "trend_up"},
}

REWORK_TARGETS: dict[str, Any] = {
    "first_pass_preflight_rate": ">=0.90",
    "merge_race_count": 0,
    "post_merge_correctness_hotfix_rate": "<0.05",
    "median_implementation_to_merge_seconds": "trend_down_without_quality_regression",
}


def build_portfolio_kpi_scorecard(
    store: dict[str, Any],
    *,
    rework_records: list[dict[str, Any]] | None = None,
    eligible_projects: list[str] | None = None,
) -> dict[str, Any]:
    """Build a fail-closed product-portfolio KPI view from existing evidence.

    The scorecard deliberately creates no second metrics store. Delivery metrics
    come from the canonical compounding time-series projection, rework metrics
    come from explicit throughput-guard records, and bottleneck ranking delegates
    to the existing measured-only optimizer.

    Historical windows for ``jweter/project-orchestrator`` are intentionally
    excluded from product KPIs. The control plane is not a fourth product and its
    own merge performance must never make the three-product portfolio appear
    measured or healthy when product evidence is absent.
    """
    if eligible_projects is None:
        eligible = list(PROJECT_KEYS)
    else:
        if not isinstance(eligible_projects, list):
            raise ValueError("eligible_projects must be a list")
        unknown = sorted(set(eligible_projects) - set(PROJECT_KEYS))
        if unknown:
            raise ValueError(f"eligible_projects contains unknown projects: {', '.join(unknown)}")
        eligible = sorted(set(eligible_projects))

    source_windows = store.get("windows", [])
    if not isinstance(source_windows, list):
        raise ValueError("timeseries store windows must be a list")
    product_windows = [
        window
        for window in source_windows
        if isinstance(window, dict) and window.get("project") in PROJECT_KEYS
    ]
    product_store = {"schema_version": store.get("schema_version", 1), "windows": product_windows}

    snapshot = build_reinvestment_snapshot(product_store)
    rework = compute_rework_kpis(rework_records or [])
    rework_quality = {
        key: {**value, "target": REWORK_TARGETS[key]}
        for key, value in rework.items()
        if key != "schema_version"
    }

    delivery_progress = {
        "portfolio": _delivery_scope(snapshot.get("portfolio")),
        "by_project": {
            project: _delivery_scope(snapshot.get("by_project", {}).get(project))
            for project in PROJECT_KEYS
        },
        "by_repository": {
            repository: _delivery_scope(snapshot.get("by_repository", {}).get(repository))
            for repository in sorted(EXPECTED_REPOSITORIES)
        },
    }

    bottlenecks = optimize_throughput(snapshot, eligible_projects=eligible)
    bottlenecks = _make_missing_eligible_projects_visible(bottlenecks, eligible)

    project_window_counts = {
        project: delivery_progress["by_project"][project]["window_count"]
        for project in PROJECT_KEYS
    }
    repository_window_counts = {
        repository: delivery_progress["by_repository"][repository]["window_count"]
        for repository in sorted(EXPECTED_REPOSITORIES)
    }
    latest_product_window_end = max(
        (
            window.get("window_end")
            for window in product_windows
            if isinstance(window, dict) and window.get("window_end")
        ),
        default=None,
    )

    return {
        "schema_version": 1,
        "rework_quality": rework_quality,
        "delivery_progress": delivery_progress,
        "bottlenecks": bottlenecks,
        "evidence_coverage": {
            "product_windows": snapshot.get("generated_from_window_count", 0),
            "source_store_windows": len(source_windows),
            "orchestrator_only_windows_excluded": len(source_windows) - len(product_windows),
            "latest_product_window_end": latest_product_window_end,
            "project_window_counts": project_window_counts,
            "repository_window_counts": repository_window_counts,
            "missing_product_projects": sorted(
                project for project, count in project_window_counts.items() if count == 0
            ),
            "missing_product_repositories": sorted(
                repository for repository, count in repository_window_counts.items() if count == 0
            ),
        },
        "rules": [
            "UNKNOWN and INSUFFICIENT_DATA are never converted to zero",
            "product KPIs exclude project-orchestrator windows because it is not a fourth product",
            "delivery metrics reuse the canonical compounding time-series store",
            "rework metrics require explicit evidence records",
            "bottleneck ranking is measured-only and advisory",
            "no KPI is a percent-complete, ROI, capacity, or predicted-time claim",
        ],
    }


def _delivery_scope(aggregate: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(aggregate, dict) or int(aggregate.get("window_count", 0)) == 0:
        metrics = {
            key: {
                "status": "UNKNOWN",
                "value": None,
                "unit": policy["unit"],
                "sample_count": 0,
                "reason": "no evidence windows for this scope",
                "target": policy["target"],
            }
            for key, policy in DELIVERY_METRICS.items()
        }
        return {
            "status": "INSUFFICIENT_DATA",
            "window_count": 0,
            "metrics": metrics,
        }

    raw_metrics = aggregate.get("metrics", {})
    metrics: dict[str, dict[str, Any]] = {}
    measured_count = 0
    for key, policy in DELIVERY_METRICS.items():
        metric = raw_metrics.get(key)
        if not isinstance(metric, dict):
            metric = {
                "status": "UNKNOWN",
                "value": None,
                "unit": policy["unit"],
                "sample_count": 0,
                "reason": "metric absent from aggregate evidence",
            }
        if metric.get("status") == "MEASURED":
            measured_count += 1
        metrics[key] = {**metric, "target": policy["target"]}

    return {
        "status": "MEASURED" if measured_count else "INSUFFICIENT_DATA",
        "window_count": int(aggregate.get("window_count", 0)),
        "measured_metric_count": measured_count,
        "metrics": metrics,
    }


def _make_missing_eligible_projects_visible(
    bottlenecks: dict[str, Any], eligible_projects: list[str]
) -> dict[str, Any]:
    rows = {
        row["project_id"]: row
        for row in bottlenecks.get("projects", [])
        if isinstance(row, dict) and row.get("project_id")
    }
    for project in eligible_projects:
        if project in rows:
            continue
        rows[project] = {
            "project_id": project,
            "window_count": 0,
            "state": "INSUFFICIENT_DATA",
            "reason": f"0 evidence window(s); need {MIN_WINDOWS_PER_PROJECT}",
            "measured_components": {},
            "unknown_components": [
                "ci_failure_burden",
                "failure_repair_burden",
                "merge_cycle_burden",
                "repeat_failure_burden",
            ],
            "ordinal_bottleneck_score": None,
            "evidence_refs": [],
        }
    return {**bottlenecks, "projects": [rows[key] for key in sorted(rows)]}
