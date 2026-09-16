from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PROJECTS = ("knowledge-engine", "rocksmith", "everward")
REQUEUE_EVENTS = {
    "worker_completion",
    "verification_completion",
    "default_head_change",
    "pr_state_change",
    "issue_state_change",
}


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
    negative = merge_race_zero is False or no_hotfix is False or no_repeat_regression is False or conflict_regression is True
    if negative:
        target = max(int(cfg["min_concurrent_product_lanes"]), current - int(cfg["decrease_by"]))
        return CapacityDecision(current, target, "DECREASE", "MEASURED_QUALITY_OR_REWORK_REGRESSION")

    required = (green, merge_race_zero, no_hotfix, no_repeat_regression)
    if all(value is True for value in required) and conflict_regression is False:
        target = min(int(cfg["max_concurrent_product_lanes"]), current + int(cfg["increase_by"]))
        return CapacityDecision(current, target, "INCREASE" if target > current else "HOLD", "MEASURED_GREEN_STABILITY")
    return CapacityDecision(current, current, "HOLD", "UNKNOWN_OR_MIXED_EVIDENCE")


def _normalize_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in candidates:
        project = item.get("project")
        if project not in PROJECTS:
            continue
        registry_allows = item.get("registry_ready") is not False and item.get("terminal_source") is not True
        ready = (
            item.get("state", "UNKNOWN") in {"P4", "P5"}
            and item.get("dependencies_clear") is True
            and item.get("conflict_free") is True
            and item.get("product_reality_required") is not True
            and item.get("actively_owned") is not True
            and registry_allows
        )
        normalized.append({**item, "dispatch_state": "READY" if ready else "BLOCKED"})
    return normalized


def build_execution_plan(snapshot: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_candidates(snapshot.get("candidates", []))
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
        "next_events": sorted(REQUEUE_EVENTS),
    }


def build_worker_envelopes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Create bounded worker instructions; workers never receive merge authority."""
    envelopes: list[dict[str, Any]] = []
    for packet in plan.get("selected_work_packets", []):
        envelopes.append({
            "schema_version": 1,
            "packet_id": packet["id"],
            "project": packet["project"],
            "repository": packet.get("repository"),
            "base_sha": packet.get("base_sha"),
            "branch": packet.get("branch"),
            "paths": packet.get("paths", []),
            "task": packet.get("task"),
            "verification": packet.get("verification", []),
            "capabilities_to_reuse": packet.get("capabilities_to_reuse", []),
            "merge_authority": False,
            "product_reality_authority": False,
            "result_required": ["packet_id", "repository", "branch", "exact_head_sha", "state", "verification", "changed_paths"],
        })
    return envelopes


def process_event(snapshot: dict[str, Any], policy: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """Pure event-loop transition used by hourly, webhook, worker and verification callers."""
    event_type = event.get("type")
    if event_type not in REQUEUE_EVENTS and event_type != "hourly_scheduler_run":
        return {"action": "IGNORE", "reason": "UNSUPPORTED_EVENT", "event_type": event_type}
    plan = build_execution_plan(snapshot, policy)
    return {
        "action": "DISPATCH" if plan["selected_work_packets"] else "IDLE",
        "reason": "READY_WORK" if plan["selected_work_packets"] else "NO_SAFE_READY_WORK",
        "event_type": event_type,
        "plan": plan,
        "worker_envelopes": build_worker_envelopes(plan),
    }


def reconcile_worker_result(result: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
    """Fail closed when a worker result is stale, overlapping, unverified, or claims forbidden authority."""
    required = {"packet_id", "repository", "branch", "exact_head_sha", "state", "verification", "changed_paths"}
    missing = sorted(required - result.keys())
    if missing:
        return {"state": "BLOCKED", "reason": "INCOMPLETE_WORKER_RESULT", "missing": missing}
    if result.get("merged") is True or result.get("product_reality_verified") is True:
        return {"state": "BLOCKED", "reason": "WORKER_AUTHORITY_VIOLATION"}
    if result.get("base_sha") and live.get("default_head_sha") != result.get("base_sha"):
        return {"state": "RECONCILE_REQUIRED", "reason": "STALE_DEFAULT_HEAD"}
    if live.get("overlap_detected") is True or live.get("contract_uncertain") is True:
        return {"state": "RECONCILE_REQUIRED", "reason": "OVERLAP_OR_CONTRACT_UNCERTAINTY"}
    if result.get("state") != "PREFLIGHT_GREEN":
        return {"state": "BLOCKED", "reason": "EXACT_HEAD_PREFLIGHT_NOT_GREEN"}
    return {"state": "PROMOTION_READY", "reason": "EXACT_HEAD_WORKER_RESULT_GREEN", "exact_head_sha": result["exact_head_sha"]}


def extract_learning_candidate(result: dict[str, Any]) -> dict[str, Any] | None:
    """Emit sanitized candidate metadata only after verified success; persistence remains learning-memory authority."""
    if result.get("state") not in {"PREFLIGHT_GREEN", "MERGED"} or result.get("verified") is not True:
        return None
    capability = result.get("reusable_capability")
    if not isinstance(capability, dict) or capability.get("type") not in {
        "regression_test", "deterministic_validator", "reusable_fixture", "repository_skill", "repair_rule",
        "compatibility_rule", "product_reality_automation", "test_harness", "failure_fingerprint",
    }:
        return None
    return {
        "schema_version": 1,
        "project": result.get("project"),
        "repository": result.get("repository"),
        "source_ref": result.get("source_ref"),
        "capability": capability,
        "evidence_state": "VERIFIED",
        "raw_payload_persisted": False,
    }
