from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

UNKNOWN = "UNKNOWN"
REQUIRED_METRICS = {
    "verified_merges",
    "median_cycle_hours",
    "repeat_failure_rate",
    "autonomous_completion_rate",
    "dependency_unlocks",
    "human_intervention_rate",
    "quality_regressions",
}


class CompoundingError(ValueError):
    """Raised when a compounding evidence window is malformed."""


def _nonnegative_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise CompoundingError(f"{field} must be a non-negative number")
    return float(value)


def _rate(value: Any, field: str) -> float:
    result = _nonnegative_number(value, field)
    if result > 1:
        raise CompoundingError(f"{field} must be between 0 and 1")
    return result


def validate_window(window: dict[str, Any]) -> dict[str, Any]:
    required = {"window_id", "start", "end", "duration_days", "evidence_state", "metrics", "evidence_refs"}
    missing = sorted(required - set(window))
    if missing:
        raise CompoundingError(f"window missing required fields: {missing}")
    for field in ("window_id", "start", "end"):
        if not isinstance(window[field], str) or not window[field]:
            raise CompoundingError(f"{field} must be a non-empty string")
    if window["evidence_state"] != "VERIFIED":
        raise CompoundingError("only VERIFIED evidence windows may be persisted")
    duration = window["duration_days"]
    if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
        raise CompoundingError("duration_days must be a positive integer")
    refs = window["evidence_refs"]
    if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) and ref for ref in refs):
        raise CompoundingError("evidence_refs must be a non-empty string list")
    metrics = window["metrics"]
    if not isinstance(metrics, dict) or set(metrics) != REQUIRED_METRICS:
        raise CompoundingError("metrics must contain exactly the canonical metric set")

    normalized_metrics = {
        "verified_merges": _nonnegative_number(metrics["verified_merges"], "verified_merges"),
        "median_cycle_hours": _nonnegative_number(metrics["median_cycle_hours"], "median_cycle_hours"),
        "repeat_failure_rate": _rate(metrics["repeat_failure_rate"], "repeat_failure_rate"),
        "autonomous_completion_rate": _rate(metrics["autonomous_completion_rate"], "autonomous_completion_rate"),
        "dependency_unlocks": _nonnegative_number(metrics["dependency_unlocks"], "dependency_unlocks"),
        "human_intervention_rate": _rate(metrics["human_intervention_rate"], "human_intervention_rate"),
        "quality_regressions": _nonnegative_number(metrics["quality_regressions"], "quality_regressions"),
    }
    if normalized_metrics["median_cycle_hours"] <= 0:
        raise CompoundingError("median_cycle_hours must be greater than zero")

    return {
        "window_id": window["window_id"],
        "start": window["start"],
        "end": window["end"],
        "duration_days": duration,
        "evidence_state": "VERIFIED",
        "metrics": normalized_metrics,
        "evidence_refs": list(refs),
    }


def validate_series(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != 1:
        raise CompoundingError("compounding-series schema_version must be 1")
    windows = payload.get("windows")
    if not isinstance(windows, list):
        raise CompoundingError("windows must be a list")
    normalized = [validate_window(window) for window in windows]
    ids = [window["window_id"] for window in normalized]
    if len(ids) != len(set(ids)):
        raise CompoundingError("window_id values must be unique")
    normalized.sort(key=lambda window: (window["start"], window["end"], window["window_id"]))
    return {"schema_version": 1, "windows": normalized}


def load_series(path: str | Path) -> dict[str, Any]:
    return validate_series(json.loads(Path(path).read_text(encoding="utf-8")))


def add_window(series: dict[str, Any], window: dict[str, Any]) -> dict[str, Any]:
    normalized = validate_series(series)
    candidate = validate_window(window)
    if any(existing["window_id"] == candidate["window_id"] for existing in normalized["windows"]):
        raise CompoundingError(f"duplicate window_id: {candidate['window_id']}")
    return validate_series({"schema_version": 1, "windows": [*normalized["windows"], candidate]})


def write_series(path: str | Path, series: dict[str, Any]) -> None:
    normalized = validate_series(series)
    Path(path).write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _capacity_index(window: dict[str, Any]) -> float:
    metrics = window["metrics"]
    return metrics["verified_merges"] / metrics["median_cycle_hours"]


def evaluate_compounding(series: dict[str, Any]) -> dict[str, Any]:
    normalized = validate_series(series)
    windows = normalized["windows"]
    if len(windows) < 3:
        return {
            "schema_version": 1,
            "claim_state": UNKNOWN,
            "reason": "At least three verified comparable windows are required.",
            "window_count": len(windows),
            "capacity_growth_factors": [],
            "geometric_mean_capacity_growth": UNKNOWN,
        }

    latest = windows[-3:]
    durations = {window["duration_days"] for window in latest}
    if len(durations) != 1:
        return {
            "schema_version": 1,
            "claim_state": UNKNOWN,
            "reason": "The latest three evidence windows are not comparable in duration.",
            "window_count": len(windows),
            "capacity_growth_factors": [],
            "geometric_mean_capacity_growth": UNKNOWN,
        }

    capacities = [_capacity_index(window) for window in latest]
    if any(value <= 0 for value in capacities):
        return {
            "schema_version": 1,
            "claim_state": UNKNOWN,
            "reason": "Verified throughput/cycle evidence cannot produce positive capacity ratios.",
            "window_count": len(windows),
            "capacity_growth_factors": [],
            "geometric_mean_capacity_growth": UNKNOWN,
        }

    growth = [capacities[index] / capacities[index - 1] for index in range(1, len(capacities))]
    geometric = math.prod(growth) ** (1 / len(growth))

    metrics = [window["metrics"] for window in latest]
    quality_clear = all(item["quality_regressions"] == 0 for item in metrics)
    repeat_nonincreasing = all(
        metrics[index]["repeat_failure_rate"] <= metrics[index - 1]["repeat_failure_rate"]
        for index in range(1, len(metrics))
    )
    autonomy_nondecreasing = all(
        metrics[index]["autonomous_completion_rate"] >= metrics[index - 1]["autonomous_completion_rate"]
        for index in range(1, len(metrics))
    )
    intervention_nonincreasing = all(
        metrics[index]["human_intervention_rate"] <= metrics[index - 1]["human_intervention_rate"]
        for index in range(1, len(metrics))
    )
    capacity_multiplicative = all(factor > 1 for factor in growth)

    supported = (
        quality_clear
        and repeat_nonincreasing
        and autonomy_nondecreasing
        and intervention_nonincreasing
        and capacity_multiplicative
    )
    return {
        "schema_version": 1,
        "claim_state": "SUPPORTED_BY_CURRENT_EVIDENCE" if supported else "NOT_SUPPORTED",
        "claim_caveat": (
            "Current evidence is consistent with sustained multiplicative engineering-capacity growth; "
            "this is empirical support, not mathematical proof of indefinite exponential development."
            if supported
            else "The current verified windows do not satisfy every conservative compounding criterion."
        ),
        "window_ids": [window["window_id"] for window in latest],
        "window_count": len(windows),
        "capacity_growth_factors": growth,
        "geometric_mean_capacity_growth": geometric,
        "criteria": {
            "quality_regressions_zero": quality_clear,
            "repeat_failure_rate_nonincreasing": repeat_nonincreasing,
            "autonomous_completion_rate_nondecreasing": autonomy_nondecreasing,
            "human_intervention_rate_nonincreasing": intervention_nonincreasing,
            "capacity_growth_multiplicative": capacity_multiplicative,
        },
    }
