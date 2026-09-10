from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

PROMOTION_ORDER = (
    "RESERVED",
    "IMPLEMENTING",
    "PREFLIGHT_UNVERIFIED",
    "PREFLIGHT_GREEN",
    "PR_OPEN",
    "CI_PENDING",
    "GREEN",
    "MERGED",
)

NO_ADVANCEMENT_ESCALATION_RUNS = 2


def apply_watchdog_run(
    *,
    state: dict[str, Any] | None,
    run: dict[str, Any],
    escalation_runs: int = NO_ADVANCEMENT_ESCALATION_RUNS,
) -> dict[str, Any]:
    """Update durable lane liveness and flow telemetry from explicit run evidence.

    The watchdog never invents timestamps, eligibility, blocker causes, or
    Product Reality. It only derives counters and transition durations from
    evidence supplied by the scheduler.
    """
    if isinstance(escalation_runs, bool) or not isinstance(escalation_runs, int) or escalation_runs < 2:
        raise ValueError("escalation_runs must be an integer >= 2")

    observed_at = _require_timestamp(run.get("observed_at"), "run.observed_at")
    lanes = run.get("lanes")
    if not isinstance(lanes, list) or not lanes:
        raise ValueError("run.lanes must be a non-empty list")

    updated = _normalize_state(state)
    actions: list[dict[str, Any]] = []

    seen: set[str] = set()
    for evidence in lanes:
        if not isinstance(evidence, dict):
            raise ValueError("every lane must be an object")
        lane_id = _require_text(evidence.get("lane_id"), "lane.lane_id")
        if lane_id in seen:
            raise ValueError(f"duplicate lane_id: {lane_id}")
        seen.add(lane_id)

        project = _require_text(evidence.get("project"), f"{lane_id}.project")
        promotion_state = _require_text(
            evidence.get("promotion_state"), f"{lane_id}.promotion_state"
        )
        if promotion_state not in PROMOTION_ORDER and promotion_state not in {
            "PRODUCT_REALITY_PENDING",
            "PRODUCT_REALITY_VERIFIED",
            "NO_SAFE_ELIGIBLE_WORK",
        }:
            raise ValueError(f"{lane_id}.promotion_state is invalid")

        eligible = evidence.get("eligible")
        if not isinstance(eligible, bool):
            raise ValueError(f"{lane_id}.eligible must be boolean")
        advanced = evidence.get("advanced")
        if not isinstance(advanced, bool):
            raise ValueError(f"{lane_id}.advanced must be boolean")

        next_gate = _require_text(evidence.get("next_gate"), f"{lane_id}.next_gate")
        blocker = evidence.get("blocker_fingerprint")
        if blocker is not None:
            blocker = _require_text(blocker, f"{lane_id}.blocker_fingerprint")

        prior = deepcopy(updated["lanes"].get(lane_id, {}))
        lane = {
            "project": project,
            "promotion_state": promotion_state,
            "next_gate": next_gate,
            "eligible": eligible,
            "last_observed_at": observed_at,
            "last_advancement_at": prior.get("last_advancement_at"),
            "blocker_fingerprint": blocker,
            "consecutive_same_blocker_no_advancement_runs": 0,
            "promotion_history": list(prior.get("promotion_history", [])),
            "flow_durations_seconds": dict(prior.get("flow_durations_seconds", {})),
        }

        if not lane["promotion_history"]:
            lane["promotion_history"].append(
                {"state": promotion_state, "entered_at": observed_at}
            )
        elif lane["promotion_history"][-1]["state"] != promotion_state:
            previous = lane["promotion_history"][-1]
            duration = _seconds_between(previous["entered_at"], observed_at)
            transition_key = f'{previous["state"]}->{promotion_state}'
            lane["flow_durations_seconds"][transition_key] = duration
            lane["promotion_history"].append(
                {"state": promotion_state, "entered_at": observed_at}
            )

        if advanced:
            lane["last_advancement_at"] = observed_at
            lane["blocker_fingerprint"] = None
            lane["consecutive_same_blocker_no_advancement_runs"] = 0
        elif eligible:
            if blocker is None:
                raise ValueError(
                    f"{lane_id} is eligible and did not advance; blocker_fingerprint is required"
                )
            same_blocker = prior.get("blocker_fingerprint") == blocker
            previous_count = prior.get(
                "consecutive_same_blocker_no_advancement_runs", 0
            )
            count = previous_count + 1 if same_blocker else 1
            lane["consecutive_same_blocker_no_advancement_runs"] = count

            if count >= escalation_runs:
                actions.append(
                    {
                        "priority": "P1",
                        "kind": "ORCHESTRATION_DEFECT",
                        "lane_id": lane_id,
                        "project": project,
                        "blocker_fingerprint": blocker,
                        "consecutive_runs": count,
                        "required_action": (
                            "change execution strategy or repair the control plane/process "
                            "before another passive report of this blocker"
                        ),
                    }
                )
            else:
                actions.append(
                    {
                        "priority": "ROTATE",
                        "kind": "PARKED_FOR_RETRY",
                        "lane_id": lane_id,
                        "project": project,
                        "blocker_fingerprint": blocker,
                        "consecutive_runs": count,
                        "required_action": (
                            "park only this dependent lane and service another eligible lane"
                        ),
                    }
                )

        updated["lanes"][lane_id] = lane

    updated["last_run_observed_at"] = observed_at
    updated["actions"] = actions
    updated["portfolio"] = _portfolio_summary(updated["lanes"])
    return updated


def _portfolio_summary(lanes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    eligible = [lane for lane in lanes.values() if lane["eligible"]]
    stale = [
        lane
        for lane in lanes.values()
        if lane["eligible"]
        and lane["consecutive_same_blocker_no_advancement_runs"]
        >= NO_ADVANCEMENT_ESCALATION_RUNS
    ]
    return {
        "eligible_lane_count": len(eligible),
        "p1_orchestration_defect_lane_count": len(stale),
        "flow_metrics_policy": (
            "durations are emitted only for observed promotion transitions with explicit timestamps"
        ),
    }


def _normalize_state(state: dict[str, Any] | None) -> dict[str, Any]:
    if state is None:
        return {
            "schema_version": 1,
            "last_run_observed_at": None,
            "lanes": {},
            "actions": [],
            "portfolio": {},
        }
    if not isinstance(state, dict):
        raise ValueError("state must be an object")
    if state.get("schema_version") != 1:
        raise ValueError("state.schema_version must be 1")
    lanes = state.get("lanes")
    if not isinstance(lanes, dict):
        raise ValueError("state.lanes must be an object")
    normalized = deepcopy(state)
    normalized.setdefault("actions", [])
    normalized.setdefault("portfolio", {})
    return normalized


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _require_timestamp(value: Any, field: str) -> str:
    value = _require_text(value, field)
    _parse_timestamp(value)
    return value


def _parse_timestamp(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include timezone information")
    return parsed.astimezone(timezone.utc)


def _seconds_between(start: str, end: str) -> int:
    seconds = int((_parse_timestamp(end) - _parse_timestamp(start)).total_seconds())
    if seconds < 0:
        raise ValueError("promotion timestamps must be monotonic")
    return seconds
