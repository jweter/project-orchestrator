from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PROJECTS = ("knowledge-engine", "rocksmith", "everward")


@dataclass(frozen=True)
class CapacityDecision:
    current: int
    target: int
    action: str
    reason: str


def _known_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def capacity_decision(evidence: dict[str, Any], policy: dict[str, Any]) -> CapacityDecision:
    cfg = policy["capacity_controller"]
    current = int(evidence.get("current_capacity", cfg["initial_max_concurrent_product_lanes"]))
    windows = int(evidence.get("comparable_evidence_windows", 0))
    if windows < int(cfg["minimum_evidence_windows_before_change"]):
        return CapacityDecision(current, current, "HOLD", "INSUFFICIENT_DATA")

    green = _known_bool(evidence.get("all_sampled_required_verification_green"))
    merge_race_zero = _known_bool(evidence.get("merge_race_count_zero_when_measured"))
    no_hotfix = _known_bool(evidence.get("no_measured_post_merge_correctness_hotfix"))
    no_repeat_regression = _known_bool(evidence.get("no_measured_repeat_failure_regression"))
    conflict_regression = _known_bool(evidence.get("reconciliation_conflict_rate_increase"))

    negative = (
        merge_race_zero is False
        or no_hotfix is False
        or no_repeat_regression is False
        or conflict_regression is True
    )
    if negative:
        target = max(int(cfg["min_concurrent_product_lanes"]), current - int(cfg["decrease_by"]))
        return CapacityDecision(current, target, "DECREASE", "MEASURED_QUALITY_OR_REWORK_REGRESSION")

    required = (green, merge_race_zero, no_hotfix, no_repeat_regression)
    if all(value is True for value in required) and conflict_regression is False:
        target = min(int(cfg["max_concurrent_product_lanes"]), current + int(cfg["increase_by"]))
        action = "INCREASE" if target > current else "HOLD"
        return CapacityDecision(current, target, action, "MEASURED_GREEN_STABILITY")

    return CapacityDecision(current, current, "HOLD", "UNKNOWN_OR_MIXED_EVIDENCE")


def build_execution_plan(snapshot: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    candidates = snapshot.get("candidates", [])
    normalized: list[dict[str, Any]] = []
    for item in candidates:
        project = item.get("project")
        if project not in PROJECTS:
            continue
        state = item.get("state", "UNKNOWN")
        dependencies_clear = item.get("dependencies_clear") is True
        conflict_free = item.get("conflict_free") is True
        product_reality_required = item.get("product_reality_required") is True
        ready = state in {"P4", "P5"} and dependencies_clear and conflict_free and not product_reality_required
        normalized.append({**item, "dispatch_state": "READY" if ready else "BLOCKED"})

    by_project: dict[str, list[dict[str, Any]]] = {project: [] for project in PROJECTS}
    for item in normalized:
        by_project[item["project"]].append(item)

    selected: list[dict[str, Any]] = []
    for project in PROJECTS:
        ready = [item for item in by_project[project] if item["dispatch_state"] == "READY"]
        ready.sort(key=lambda item: (int(item.get("dependency_level", 999)), -int(item.get("downstream_unlock_count", 0)), str(item.get("id", ""))))
        if ready:
            selected.append(ready[0])

    capacity = capacity_decision(snapshot.get("capacity_evidence", {}), policy)
    selected = selected[: capacity.target]
    return {
        "schema_version": 1,
        "engine": policy["name"],
        "candidate_count": len(normalized),
        "eligible_work_packets": sum(1 for item in normalized if item["dispatch_state"] == "READY"),
        "selected_work_packets": selected,
        "productive_parallel_lane_target": min(len(selected), capacity.target),
        "capacity": capacity.__dict__,
        "blocked_work_packets": [item for item in normalized if item["dispatch_state"] == "BLOCKED"],
        "next_events": ["worker_completion", "verification_completion", "default_head_change", "pr_state_change", "issue_state_change"],
    }
