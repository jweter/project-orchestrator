from __future__ import annotations

from typing import Any

SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4, "P5": 5, "P6": 6, "ROTATE": 7}


def build_daily_bottleneck_review(
    *,
    watchdog_state: dict[str, Any],
    observed_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a fail-closed daily bottleneck review from explicit evidence.

    The report selects the largest observed blocker by priority first, then
    recurrence count. It never invents root cause. When root cause is not
    established, the required remediation is to gather the missing evidence
    and rotate parallel-safe work meanwhile.
    """
    if watchdog_state.get("schema_version") != 1:
        raise ValueError("watchdog_state.schema_version must be 1")
    lanes = watchdog_state.get("lanes")
    if not isinstance(lanes, dict):
        raise ValueError("watchdog_state.lanes must be an object")

    events = observed_events or []
    if not isinstance(events, list):
        raise ValueError("observed_events must be a list")

    blockers: list[dict[str, Any]] = []
    for lane_id, lane in lanes.items():
        fingerprint = lane.get("blocker_fingerprint")
        count = lane.get("consecutive_same_blocker_no_advancement_runs", 0)
        if fingerprint and lane.get("eligible"):
            action = _action_for_lane(watchdog_state.get("actions", []), lane_id)
            blockers.append(
                {
                    "lane_id": lane_id,
                    "project": lane.get("project", "UNKNOWN"),
                    "fingerprint": fingerprint,
                    "consecutive_runs": count,
                    "priority": action.get("priority", "ROTATE"),
                    "next_gate": lane.get("next_gate", "UNKNOWN"),
                    "last_advancement_at": lane.get("last_advancement_at"),
                    "root_cause": _root_cause(events, lane_id, fingerprint),
                }
            )

    blockers.sort(
        key=lambda item: (
            SEVERITY_ORDER.get(item["priority"], 99),
            -item["consecutive_runs"],
            item["project"],
            item["lane_id"],
        )
    )

    biggest = blockers[0] if blockers else None
    remediation = _remediation(biggest) if biggest else {
        "mode": "NO_ACTIVE_BLOCKER",
        "action": "continue highest-value eligible product execution",
    }

    return {
        "schema_version": 1,
        "review_type": "DAILY_BOTTLENECK_REVIEW",
        "largest_blocker": biggest,
        "active_blockers": blockers,
        "root_cause_policy": (
            "ESTABLISHED only from explicit evidence; otherwise UNKNOWN with an exact evidence-acquisition gate"
        ),
        "remediation": remediation,
        "daily_rule": (
            "diagnose the largest blocker, make one bounded repair attempt when safe, "
            "then rotate to parallel-safe product work if the blocker remains"
        ),
    }


def _action_for_lane(actions: list[dict[str, Any]], lane_id: str) -> dict[str, Any]:
    for action in actions:
        if action.get("lane_id") == lane_id:
            return action
    return {}


def _root_cause(
    events: list[dict[str, Any]], lane_id: str, fingerprint: str
) -> dict[str, Any]:
    matches = [
        event
        for event in events
        if event.get("lane_id") == lane_id
        and event.get("blocker_fingerprint") == fingerprint
        and event.get("evidence_state") == "VERIFIED"
    ]
    established = [event for event in matches if event.get("root_cause")]
    if established:
        latest = established[-1]
        return {
            "status": "ESTABLISHED",
            "cause": latest["root_cause"],
            "evidence_ref": latest.get("evidence_ref", "UNKNOWN"),
        }
    return {
        "status": "UNKNOWN",
        "missing_evidence": "first meaningful failure evidence for this blocker fingerprint",
    }


def _remediation(biggest: dict[str, Any] | None) -> dict[str, Any]:
    if biggest is None:
        return {"mode": "NO_ACTIVE_BLOCKER", "action": "continue product execution"}
    root = biggest["root_cause"]
    if root["status"] == "ESTABLISHED":
        return {
            "mode": "REPAIR",
            "lane_id": biggest["lane_id"],
            "action": (
                "make one smallest-safe repair attempt against the established root cause; "
                "verify it; if still blocked, park only this lane and rotate"
            ),
        }
    return {
        "mode": "EVIDENCE_THEN_ROTATE",
        "lane_id": biggest["lane_id"],
        "action": (
            "acquire the first meaningful failure evidence once; do not guess root cause; "
            "if evidence remains unavailable, park only this lane and rotate"
        ),
    }
