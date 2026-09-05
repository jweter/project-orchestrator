"""Deterministic portfolio compounding evidence store.

This module measures whether the development loop is actually compounding
(rising throughput/autonomy, falling cycle time/repeat failures) instead of
asserting it. It never invents a measurement: every recorded metric is either
``MEASURED`` with reproducible evidence references, or explicitly
``UNKNOWN``/``INSUFFICIENT_DATA``.

This module intentionally has no I/O side effects beyond explicit
load/write helpers and performs no GitHub collection itself, mirroring the
existing collection/decision separation in ``github_evidence``/``decision``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .inventory import EXPECTED_REPOSITORIES
from .learning import ALLOWED_PROJECTS, _contains_secret

ALLOWED_REPOSITORIES = EXPECTED_REPOSITORIES | {"jweter/project-orchestrator"}

# unit is fixed per metric so a mismatched unit is a schema violation rather
# than a silent unit-confusion bug when aggregating across windows.
METRIC_UNITS: dict[str, str] = {
    "pr_creation_to_green_seconds": "seconds",
    "failure_to_repair_seconds": "seconds",
    "merge_cycle_seconds": "seconds",
    "blocked_waiting_seconds": "seconds",
    "ci_failure_count": "count",
    "ci_repeat_failure_count": "count",
    "preflight_failures_prevented_count": "count",
    "roadmap_slices_completed_count": "count",
    "product_reality_debt_created_count": "count",
    "product_reality_debt_resolved_count": "count",
    "cross_repo_incident_count": "count",
    "autonomous_intervention_count": "count",
    "jeremy_required_intervention_count": "count",
}
ALLOWED_METRIC_KEYS = frozenset(METRIC_UNITS)
METRIC_STATUSES = {"MEASURED", "UNKNOWN", "INSUFFICIENT_DATA"}


class TimeseriesError(ValueError):
    """Raised when a compounding-evidence record violates the deterministic contract."""


def _require_iso8601(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise TimeseriesError(f"{field} must be a non-empty ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TimeseriesError(f"{field} is not valid ISO-8601: {value!r}") from exc
    if parsed.tzinfo is None:
        raise TimeseriesError(f"{field} must be timezone-aware: {value!r}")
    return parsed.astimezone(timezone.utc)


def validate_metric(key: str, entry: Any) -> dict[str, Any]:
    if key not in METRIC_UNITS:
        raise TimeseriesError(f"unknown metric key: {key}")
    if not isinstance(entry, dict):
        raise TimeseriesError(f"metric {key} must be an object")

    status = entry.get("status")
    if status not in METRIC_STATUSES:
        raise TimeseriesError(f"metric {key} has unsupported status: {status!r}")

    if status == "UNKNOWN":
        if set(entry) != {"status"}:
            raise TimeseriesError(f"metric {key} UNKNOWN must carry no other fields")
        return {"status": "UNKNOWN"}

    if status == "INSUFFICIENT_DATA":
        if set(entry) != {"status", "reason"}:
            raise TimeseriesError(f"metric {key} INSUFFICIENT_DATA must carry only 'reason'")
        reason = entry["reason"]
        if not isinstance(reason, str) or not reason.strip():
            raise TimeseriesError(f"metric {key} reason must be a non-empty string")
        return {"status": "INSUFFICIENT_DATA", "reason": reason}

    # MEASURED
    required = {"status", "value", "unit", "sample_count", "evidence_refs"}
    if set(entry) != required:
        raise TimeseriesError(f"metric {key} MEASURED must carry exactly {sorted(required)}")

    value = entry["value"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TimeseriesError(f"metric {key} value must be numeric")
    if not _is_finite(value):
        raise TimeseriesError(f"metric {key} value must be finite")
    if value < 0:
        raise TimeseriesError(f"metric {key} value must not be negative")

    unit = entry["unit"]
    if unit != METRIC_UNITS[key]:
        raise TimeseriesError(f"metric {key} unit must be {METRIC_UNITS[key]!r}, got {unit!r}")

    sample_count = entry["sample_count"]
    if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count < 1:
        raise TimeseriesError(f"metric {key} sample_count must be a positive integer")

    evidence_refs = entry["evidence_refs"]
    if not isinstance(evidence_refs, list) or not evidence_refs:
        raise TimeseriesError(f"metric {key} MEASURED requires non-empty evidence_refs")
    for ref in evidence_refs:
        if not isinstance(ref, str) or not ref.strip():
            raise TimeseriesError(f"metric {key} evidence_refs must be non-empty strings")

    return {
        "status": "MEASURED",
        "value": value,
        "unit": unit,
        "sample_count": sample_count,
        "evidence_refs": sorted(set(evidence_refs)),
    }


def _is_finite(value: float) -> bool:
    return value == value and value not in (float("inf"), float("-inf"))


def _window_identity(window: dict[str, Any]) -> str:
    stable = {
        "project": window["project"],
        "repository": window["repository"],
        "window_start": window["window_start"],
        "window_end": window["window_end"],
        "metrics": window["metrics"],
    }
    encoded = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def validate_window(window: Any) -> dict[str, Any]:
    if not isinstance(window, dict):
        raise TimeseriesError("evidence window must be an object")

    required_fields = {"project", "repository", "window_start", "window_end", "metrics"}
    optional_fields = {"notes", "window_id"}
    unknown = set(window) - required_fields - optional_fields
    if unknown:
        raise TimeseriesError(f"evidence window has unknown fields: {sorted(unknown)}")
    missing = sorted(required_fields - set(window))
    if missing:
        raise TimeseriesError(f"evidence window missing required fields: {missing}")

    project = window["project"]
    if project not in ALLOWED_PROJECTS:
        raise TimeseriesError(f"evidence window project is not in the portfolio: {project!r}")

    repository = window["repository"]
    if repository not in ALLOWED_REPOSITORIES:
        raise TimeseriesError(f"evidence window repository is not in the portfolio: {repository!r}")

    start = _require_iso8601(window["window_start"], "window_start")
    end = _require_iso8601(window["window_end"], "window_end")
    if end <= start:
        raise TimeseriesError("window_end must be strictly after window_start")

    metrics_in = window["metrics"]
    if not isinstance(metrics_in, dict):
        raise TimeseriesError("metrics must be an object")
    missing_metrics = sorted(ALLOWED_METRIC_KEYS - set(metrics_in))
    if missing_metrics:
        raise TimeseriesError(
            f"evidence window omits metric keys (use UNKNOWN explicitly): {missing_metrics}"
        )
    extra_metrics = sorted(set(metrics_in) - ALLOWED_METRIC_KEYS)
    if extra_metrics:
        raise TimeseriesError(f"evidence window has unsupported metric keys: {extra_metrics}")

    metrics = {key: validate_metric(key, metrics_in[key]) for key in sorted(metrics_in)}

    notes = window.get("notes")
    if notes is not None:
        if not isinstance(notes, str):
            raise TimeseriesError("notes must be a string when present")
        if _contains_secret(notes):
            raise TimeseriesError("notes appear to contain secret material")

    normalized: dict[str, Any] = {
        "project": project,
        "repository": repository,
        "window_start": window["window_start"],
        "window_end": window["window_end"],
        "metrics": metrics,
    }
    if notes:
        normalized["notes"] = notes
    normalized["window_id"] = _window_identity(normalized)

    supplied_id = window.get("window_id")
    if supplied_id is not None and supplied_id != normalized["window_id"]:
        raise TimeseriesError("window_id does not match verified content")

    return normalized


def validate_store(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TimeseriesError("compounding-timeseries store must be an object")
    if payload.get("schema_version") != 1:
        raise TimeseriesError("compounding-timeseries schema_version must be 1")
    windows = payload.get("windows")
    if not isinstance(windows, list):
        raise TimeseriesError("compounding-timeseries windows must be a list")

    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    for raw in windows:
        window = validate_window(raw)
        if window["window_id"] in ids:
            raise TimeseriesError(f"duplicate window_id: {window['window_id']}")
        ids.add(window["window_id"])
        normalized.append(window)

    normalized.sort(key=lambda w: (w["project"], w["repository"], w["window_start"], w["window_id"]))
    return {"schema_version": 1, "windows": normalized}


def load_store(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_store(payload)


def write_store(path: str | Path, store: dict[str, Any]) -> None:
    normalized = validate_store(store)
    Path(path).write_text(
        json.dumps(normalized, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def append_window(store: dict[str, Any], window: dict[str, Any]) -> dict[str, Any]:
    normalized_store = validate_store(store)
    normalized_window = validate_window(window)
    if any(w["window_id"] == normalized_window["window_id"] for w in normalized_store["windows"]):
        return normalized_store
    merged = [*normalized_store["windows"], normalized_window]
    return validate_store({"schema_version": 1, "windows": merged})


def recent_windows(
    store: dict[str, Any],
    *,
    since: str | None = None,
    project: str | None = None,
    repository: str | None = None,
) -> list[dict[str, Any]]:
    """Return windows sorted most-recent-first, for DoWork/report queries."""
    normalized = validate_store(store)
    cutoff = _require_iso8601(since, "since") if since is not None else None

    selected = []
    for window in normalized["windows"]:
        if project is not None and window["project"] != project:
            continue
        if repository is not None and window["repository"] != repository:
            continue
        if cutoff is not None and _require_iso8601(window["window_end"], "window_end") < cutoff:
            continue
        selected.append(window)

    selected.sort(key=lambda w: (w["window_end"], w["window_id"]), reverse=True)
    return selected


def _aggregate_metric(key: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    measured = [e for e in entries if e["status"] == "MEASURED"]
    insufficient = [e for e in entries if e["status"] == "INSUFFICIENT_DATA"]
    unknown_count = sum(1 for e in entries if e["status"] == "UNKNOWN")

    if not measured:
        if insufficient:
            return {
                "status": "INSUFFICIENT_DATA",
                "reason": (
                    f"no MEASURED windows for {key}: "
                    f"{len(insufficient)} INSUFFICIENT_DATA, {unknown_count} UNKNOWN"
                ),
                "window_count": len(entries),
            }
        return {"status": "UNKNOWN", "window_count": len(entries)}

    total_samples = sum(e["sample_count"] for e in measured)
    weighted_sum = sum(e["value"] * e["sample_count"] for e in measured)
    evidence_refs = sorted({ref for e in measured for ref in e["evidence_refs"]})
    return {
        "status": "MEASURED",
        "value": weighted_sum / total_samples,
        "unit": METRIC_UNITS[key],
        "sample_count": total_samples,
        "evidence_refs": evidence_refs,
        "window_count": len(entries),
        "measured_window_count": len(measured),
        "insufficient_window_count": len(insufficient),
        "unknown_window_count": unknown_count,
    }


def aggregate_windows(
    windows: list[dict[str, Any]],
    *,
    group_by: str = "repository",
) -> dict[str, dict[str, Any]]:
    """Deterministically roll windows up by ``project``, ``repository``, or ``portfolio``.

    Input order never affects the result: windows are re-sorted by
    ``window_id`` before folding, so replaying the same evidence set always
    produces byte-identical aggregates.
    """
    if group_by not in {"repository", "project", "portfolio"}:
        raise TimeseriesError(f"unsupported group_by: {group_by!r}")

    normalized_windows = [validate_window(w) for w in windows]
    normalized_windows.sort(key=lambda w: w["window_id"])

    groups: dict[str, list[dict[str, Any]]] = {}
    for window in normalized_windows:
        if group_by == "portfolio":
            key = "portfolio"
        elif group_by == "project":
            key = window["project"]
        else:
            key = window["repository"]
        groups.setdefault(key, []).append(window)

    result: dict[str, dict[str, Any]] = {}
    for key in sorted(groups):
        members = groups[key]
        metrics = {
            metric_key: _aggregate_metric(
                metric_key, [w["metrics"][metric_key] for w in members]
            )
            for metric_key in sorted(ALLOWED_METRIC_KEYS)
        }
        result[key] = {
            "window_count": len(members),
            "window_ids": sorted(w["window_id"] for w in members),
            "metrics": metrics,
        }
    return result


def build_reinvestment_snapshot(
    store: dict[str, Any],
    *,
    since: str | None = None,
) -> dict[str, Any]:
    """Package the interface the future Reinvestment Controller will consume.

    This is a deterministic, read-only projection of measured evidence — it
    contains no reinvestment decision or policy. It exists so the next
    roadmap slice (the Reinvestment Controller) has a stable, documented
    shape to build against without this slice needing to guess that
    controller's internals.
    """
    normalized = validate_store(store)
    windows = recent_windows(normalized, since=since)
    return {
        "schema_version": 1,
        "generated_from_window_count": len(windows),
        "since": since,
        "portfolio": aggregate_windows(windows, group_by="portfolio").get(
            "portfolio", {"window_count": 0, "window_ids": [], "metrics": {}}
        ),
        "by_project": aggregate_windows(windows, group_by="project"),
        "by_repository": aggregate_windows(windows, group_by="repository"),
    }
