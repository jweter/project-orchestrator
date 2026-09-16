"""Evidence-first promotion-flow observability.

This module makes GREEN, READY_TO_MERGE, and MERGED distinct states and
computes waiting-time KPIs only from explicit timestamps.  It is deliberately
pure: it does not merge PRs, weaken gates, or infer missing evidence.
"""
from __future__ import annotations

from datetime import datetime
import statistics
from typing import Any

FLOW_EVENTS = (
    "issue_selected_at",
    "implementation_started_at",
    "first_preflight_started_at",
    "first_preflight_green_at",
    "pr_opened_at",
    "first_ci_started_at",
    "first_ci_green_at",
    "latest_head_ci_green_at",
    "reviews_clear_at",
    "blocking_comments_clear_at",
    "mergeability_clear_at",
    "ready_to_merge_at",
    "merged_at",
    "product_reality_pending_at",
    "product_reality_verified_at",
    "branch_reconciled_at",
    "branch_deleted_at",
)

READY_GATES = (
    "fresh_required_ci_green",
    "mergeability_clear",
    "required_reviews_clear",
    "blocking_comments_clear",
    "active_work_lease_clear",
    "policy_boundaries_clear",
    "required_premerge_product_reality_clear",
)


def _time(value: str) -> datetime:
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("flow timestamps must include a timezone")
    return parsed


def _duration(record: dict[str, Any], start: str, end: str) -> float | None:
    if not record.get(start) or not record.get(end):
        return None
    seconds = (_time(record[end]) - _time(record[start])).total_seconds()
    if seconds < 0:
        raise ValueError(f"{end} cannot precede {start}")
    return seconds


def evaluate_ready_to_merge(*, head_sha: str, gates: dict[str, Any], observed_at: str) -> dict[str, Any]:
    """Return READY only when every declared safety gate is explicitly true."""
    if not isinstance(head_sha, str) or len(head_sha) != 40:
        raise ValueError("head_sha must be a 40-character SHA")
    _time(observed_at)
    missing = [gate for gate in READY_GATES if gates.get(gate) is not True]
    return {
        "schema_version": 1,
        "head_sha": head_sha,
        "state": "READY_TO_MERGE" if not missing else "NOT_READY",
        "ready_to_merge_at": observed_at if not missing else None,
        "blocking_or_unknown_gates": missing,
        "fail_closed": bool(missing),
    }


def build_flow_record(*, repository: str, pr_number: int, head_sha: str, events: dict[str, Any], merge_actor: str | None = None) -> dict[str, Any]:
    """Normalize an exact-head PR timeline without inventing absent events."""
    if not repository or not isinstance(pr_number, int) or pr_number < 1:
        raise ValueError("repository and positive pr_number are required")
    if not isinstance(head_sha, str) or len(head_sha) != 40:
        raise ValueError("head_sha must be a 40-character SHA")
    unknown = []
    normalized: dict[str, Any] = {}
    for event in FLOW_EVENTS:
        value = events.get(event)
        if value is None:
            normalized[event] = None
            unknown.append(event)
        else:
            _time(value)
            normalized[event] = value
    return {
        "schema_version": 1,
        "repository": repository,
        "pr_number": pr_number,
        "head_sha": head_sha,
        "merge_actor": merge_actor or "UNKNOWN",
        "events": normalized,
        "unknown_events": unknown,
    }


def compute_flow_kpis(records: list[dict[str, Any]], *, now: str | None = None) -> dict[str, Any]:
    """Compute promotion-flow KPIs with explicit evidence coverage."""
    durations: dict[str, list[float]] = {
        "pr_open_to_first_green_seconds": [],
        "latest_green_to_ready_seconds": [],
        "ready_to_merge_to_merge_seconds": [],
        "pr_open_to_merge_seconds": [],
        "implementation_to_merge_seconds": [],
    }
    ready_ages: list[float] = []
    now_dt = _time(now) if now else None

    for item in records:
        events = item.get("events", item)
        pairs = {
            "pr_open_to_first_green_seconds": ("pr_opened_at", "first_ci_green_at"),
            "latest_green_to_ready_seconds": ("latest_head_ci_green_at", "ready_to_merge_at"),
            "ready_to_merge_to_merge_seconds": ("ready_to_merge_at", "merged_at"),
            "pr_open_to_merge_seconds": ("pr_opened_at", "merged_at"),
            "implementation_to_merge_seconds": ("implementation_started_at", "merged_at"),
        }
        for name, (start, end) in pairs.items():
            value = _duration(events, start, end)
            if value is not None:
                durations[name].append(value)
        if now_dt and events.get("ready_to_merge_at") and not events.get("merged_at"):
            age = (now_dt - _time(events["ready_to_merge_at"])).total_seconds()
            if age < 0:
                raise ValueError("now cannot precede ready_to_merge_at")
            ready_ages.append(age)

    def metric(values: list[float]) -> dict[str, Any]:
        if not values:
            return {"status": "UNKNOWN", "sample_count": 0}
        ordered = sorted(values)
        p95_index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * 0.95)))
        return {
            "status": "MEASURED",
            "sample_count": len(values),
            "p50": statistics.median(values),
            "p95": ordered[p95_index],
            "unit": "seconds",
        }

    result = {name: metric(values) for name, values in durations.items()}
    result["ready_pr_queue_depth"] = {"status": "MEASURED", "value": len(ready_ages), "unit": "count", "sample_count": len(records)} if now_dt is not None else {"status": "UNKNOWN", "sample_count": 0}
    result["oldest_ready_pr_age_seconds"] = ({"status": "MEASURED", "value": max(ready_ages), "unit": "seconds", "sample_count": len(ready_ages)} if ready_ages else ({"status": "MEASURED", "value": 0.0, "unit": "seconds", "sample_count": len(records)} if now_dt is not None else {"status": "UNKNOWN", "sample_count": 0}))
    return {"schema_version": 1, "metrics": result}
