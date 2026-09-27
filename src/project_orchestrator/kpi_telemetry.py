"""Durable event-sourced KPI telemetry for portfolio flow.

The event ledger is append-only.  Flow reports and per-run snapshots are
projections of recorded transitions, never reconstructions from prose or guessed
state.  Existing KPI definitions/targets in :mod:`portfolio_kpis` remain
canonical; this module adds durable measurement evidence and does not redefine
those targets.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .inventory import EXPECTED_REPOSITORIES

SCHEMA_VERSION = 1
PRODUCT_PROJECTS = frozenset({"knowledge-engine", "rocksmith", "everward", "voicestudio"})
ALLOWED_PROJECTS = PRODUCT_PROJECTS | {"portfolio"}
ALLOWED_REPOSITORIES = frozenset(EXPECTED_REPOSITORIES) | {"jweter/project-orchestrator"}
ENTITY_TYPES = frozenset({"pr", "ci", "branch", "issue"})
TRANSITION_CLASSES = frozenset(
    {"NEUTRAL", "ADVANCE", "REGRESSION", "FAILURE", "REPAIR", "PROMOTION", "HYGIENE", "HUMAN_INTERVENTION"}
)
ACTOR_CLASSES = frozenset({"ORCHESTRATOR", "EXTERNAL_AUTOMATION", "HUMAN"})
MATERIAL_CLASSES = frozenset(
    {"ADVANCE", "REGRESSION", "FAILURE", "REPAIR", "PROMOTION", "HYGIENE", "HUMAN_INTERVENTION"}
)
CI_SUCCESS_STATES = frozenset({"SUCCESS", "GREEN", "PASSED"})
CI_FAILURE_STATES = frozenset({"FAILURE", "FAILED", "ERROR"})
PR_OPEN_STATES = frozenset({"DRAFT", "OPEN", "PR_OPEN"})
PR_PROMOTED_STATES = frozenset({"PREFLIGHT_GREEN", "GREEN"})
PR_MERGED_STATES = frozenset({"MERGED"})
BRANCH_PRESENT_STATES = frozenset({"PRESENT", "CREATED", "ACTIVE"})
BRANCH_DELETED_STATES = frozenset({"DELETED", "ABSENT"})
BRANCH_DEBT_STATES = frozenset({"STALE", "MERGED_UNDELETED", "ORPHANED", "HYGIENE_DEBT"})


class KpiTelemetryError(ValueError):
    """Raised when telemetry is malformed or would require guessing."""


def _parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise KpiTelemetryError(f"{field} must be a non-empty ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise KpiTelemetryError(f"{field} is not valid ISO-8601: {value!r}") from exc
    if parsed.tzinfo is None:
        raise KpiTelemetryError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _event_identity(event: dict[str, Any]) -> str:
    stable = {key: value for key, value in event.items() if key != "event_id"}
    encoded = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


def validate_event(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise KpiTelemetryError("telemetry event must be an object")
    required = {
        "observed_at",
        "run_id",
        "project",
        "repository",
        "entity_type",
        "entity_id",
        "from_state",
        "to_state",
        "transition_class",
        "actor",
        "evidence_refs",
    }
    optional = {"event_id", "metadata"}
    missing = sorted(required - set(raw))
    unknown = sorted(set(raw) - required - optional)
    if missing:
        raise KpiTelemetryError(f"telemetry event missing required fields: {missing}")
    if unknown:
        raise KpiTelemetryError(f"telemetry event has unknown fields: {unknown}")

    _parse_time(raw["observed_at"], "observed_at")
    run_id = raw["run_id"]
    if not isinstance(run_id, str) or not run_id.strip():
        raise KpiTelemetryError("run_id must be a non-empty string")
    project = raw["project"]
    if project not in ALLOWED_PROJECTS:
        raise KpiTelemetryError(f"unsupported project: {project!r}")
    repository = raw["repository"]
    if repository not in ALLOWED_REPOSITORIES:
        raise KpiTelemetryError(f"unsupported repository: {repository!r}")
    entity_type = raw["entity_type"]
    if entity_type not in ENTITY_TYPES:
        raise KpiTelemetryError(f"unsupported entity_type: {entity_type!r}")
    entity_id = raw["entity_id"]
    if not isinstance(entity_id, str) or not entity_id.strip():
        raise KpiTelemetryError("entity_id must be a non-empty string")
    from_state = raw["from_state"]
    if from_state is not None and (not isinstance(from_state, str) or not from_state.strip()):
        raise KpiTelemetryError("from_state must be null or a non-empty string")
    to_state = raw["to_state"]
    if not isinstance(to_state, str) or not to_state.strip():
        raise KpiTelemetryError("to_state must be a non-empty string")
    transition_class = raw["transition_class"]
    if transition_class not in TRANSITION_CLASSES:
        raise KpiTelemetryError(f"unsupported transition_class: {transition_class!r}")
    actor = raw["actor"]
    if actor not in ACTOR_CLASSES:
        raise KpiTelemetryError(f"unsupported actor: {actor!r}")
    evidence_refs = raw["evidence_refs"]
    if not isinstance(evidence_refs, list) or not evidence_refs:
        raise KpiTelemetryError("evidence_refs must be a non-empty list")
    if any(not isinstance(ref, str) or not ref.strip() for ref in evidence_refs):
        raise KpiTelemetryError("evidence_refs entries must be non-empty strings")
    metadata = raw.get("metadata", {})
    if not isinstance(metadata, dict):
        raise KpiTelemetryError("metadata must be an object when present")

    normalized = {
        "observed_at": raw["observed_at"],
        "run_id": run_id,
        "project": project,
        "repository": repository,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "from_state": from_state,
        "to_state": to_state,
        "transition_class": transition_class,
        "actor": actor,
        "evidence_refs": sorted(set(evidence_refs)),
    }
    if metadata:
        normalized["metadata"] = metadata
    normalized["event_id"] = _event_identity(normalized)
    supplied = raw.get("event_id")
    if supplied is not None and supplied != normalized["event_id"]:
        raise KpiTelemetryError("event_id does not match event content")
    return normalized


def load_events(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    events: list[dict[str, Any]] = []
    ids: set[str] = set()
    for line_no, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise KpiTelemetryError(f"invalid JSONL at line {line_no}") from exc
        event = validate_event(raw)
        if event["event_id"] in ids:
            raise KpiTelemetryError(f"duplicate event_id in ledger: {event['event_id']}")
        ids.add(event["event_id"])
        events.append(event)
    return events


def append_event(path: str | Path, raw: dict[str, Any]) -> dict[str, Any]:
    """Append one idempotent event without rewriting prior ledger content."""
    event = validate_event(raw)
    target = Path(path)
    existing = load_events(target)
    if any(item["event_id"] == event["event_id"] for item in existing):
        return event
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
    return event


def validate_snapshot(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise KpiTelemetryError("KPI snapshot must be an object")
    required = {"schema_version", "run_id", "generated_at", "run_event_count", "run_metrics", "rolling_reports"}
    if set(raw) != required:
        raise KpiTelemetryError(f"KPI snapshot fields must be exactly {sorted(required)}")
    if raw["schema_version"] != SCHEMA_VERSION:
        raise KpiTelemetryError("KPI snapshot schema_version must be 1")
    if not isinstance(raw["run_id"], str) or not raw["run_id"].strip():
        raise KpiTelemetryError("snapshot run_id must be a non-empty string")
    _parse_time(raw["generated_at"], "generated_at")
    if isinstance(raw["run_event_count"], bool) or not isinstance(raw["run_event_count"], int) or raw["run_event_count"] < 0:
        raise KpiTelemetryError("run_event_count must be a non-negative integer")
    if not isinstance(raw["run_metrics"], dict) or not isinstance(raw["rolling_reports"], dict):
        raise KpiTelemetryError("snapshot metrics/reports must be objects")
    return raw


def load_snapshots(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    snapshots: list[dict[str, Any]] = []
    run_ids: set[str] = set()
    for line_no, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise KpiTelemetryError(f"invalid snapshot JSONL at line {line_no}") from exc
        snapshot = validate_snapshot(raw)
        if snapshot["run_id"] in run_ids:
            raise KpiTelemetryError(f"duplicate run_id in snapshot ledger: {snapshot['run_id']}")
        run_ids.add(snapshot["run_id"])
        snapshots.append(snapshot)
    return snapshots


def append_snapshot(path: str | Path, snapshot: dict[str, Any]) -> dict[str, Any]:
    normalized = validate_snapshot(snapshot)
    target = Path(path)
    existing = load_snapshots(target)
    if any(item["run_id"] == normalized["run_id"] for item in existing):
        return next(item for item in existing if item["run_id"] == normalized["run_id"])
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(normalized, sort_keys=True, separators=(",", ":")) + "\n")
    return normalized


def _scope_events(events: Iterable[dict[str, Any]], end: datetime) -> list[dict[str, Any]]:
    return [
        event
        for event in events
        if event["project"] in PRODUCT_PROJECTS and _parse_time(event["observed_at"], "observed_at") <= end
    ]


def _in_window(events: Iterable[dict[str, Any]], start: datetime, end: datetime) -> list[dict[str, Any]]:
    return [
        event
        for event in events
        if start < _parse_time(event["observed_at"], "observed_at") <= end
    ]


def _entity_key(event: dict[str, Any]) -> tuple[str, str, str]:
    return event["repository"], event["entity_type"], event["entity_id"]


def _unknown(reason: str, *, unit: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "UNKNOWN", "value": None, "sample_count": 0, "reason": reason}
    if unit is not None:
        result["unit"] = unit
    return result


def _measured(value: float | int, sample_count: int, refs: Iterable[str], *, unit: str) -> dict[str, Any]:
    return {
        "status": "MEASURED",
        "value": value,
        "unit": unit,
        "sample_count": sample_count,
        "evidence_refs": sorted(set(refs)),
    }


def _median_latency(
    all_events: list[dict[str, Any]],
    completions: list[dict[str, Any]],
    start_states: frozenset[str],
) -> dict[str, Any]:
    seconds: list[float] = []
    refs: list[str] = []
    for completion in completions:
        key = _entity_key(completion)
        end_time = _parse_time(completion["observed_at"], "observed_at")
        candidates = [
            event
            for event in all_events
            if _entity_key(event) == key
            and event["to_state"].upper() in start_states
            and _parse_time(event["observed_at"], "observed_at") <= end_time
        ]
        if not candidates:
            continue
        start_event = max(candidates, key=lambda event: _parse_time(event["observed_at"], "observed_at"))
        delta = (end_time - _parse_time(start_event["observed_at"], "observed_at")).total_seconds()
        if delta < 0:
            continue
        seconds.append(delta)
        refs.extend(start_event["evidence_refs"])
        refs.extend(completion["evidence_refs"])
    if not seconds:
        return _unknown("no complete transition pairs in this period", unit="seconds")
    return _measured(statistics.median(seconds), len(seconds), refs, unit="seconds")


def build_flow_report(
    events: list[dict[str, Any]],
    *,
    start: str,
    end: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Build deterministic product-flow KPIs from the append-only ledger."""
    normalized = [validate_event(event) for event in events]
    start_dt = _parse_time(start, "start")
    end_dt = _parse_time(end, "end")
    if end_dt <= start_dt:
        raise KpiTelemetryError("report end must be after start")
    history = _scope_events(normalized, end_dt)
    window = _in_window(history, start_dt, end_dt)
    if run_id is not None:
        window = [event for event in window if event["run_id"] == run_id]

    issue_transitions = [
        event for event in window if event["entity_type"] == "issue" and event["transition_class"] in MATERIAL_CLASSES
    ]
    issue_entities = {_entity_key(event) for event in issue_transitions}
    advanced_issue_entities = {
        _entity_key(event)
        for event in issue_transitions
        if event["transition_class"] in {"ADVANCE", "REPAIR", "PROMOTION"}
    }
    if issue_entities:
        issue_advancement = _measured(
            len(advanced_issue_entities) / len(issue_entities),
            len(issue_entities),
            (ref for event in issue_transitions for ref in event["evidence_refs"]),
            unit="ratio",
        )
        issue_advancement["advanced_issues"] = len(advanced_issue_entities)
        issue_advancement["issues_with_material_transitions"] = len(issue_entities)
    else:
        issue_advancement = _unknown("no material issue transitions recorded in this period", unit="ratio")

    merged = [
        event for event in window if event["entity_type"] == "pr" and event["to_state"].upper() in PR_MERGED_STATES
    ]
    cycle_time = _median_latency(history, merged, PR_OPEN_STATES)
    promotion_latency = _median_latency(history, merged, PR_PROMOTED_STATES)

    successful_ci = [
        event for event in window if event["entity_type"] == "ci" and event["to_state"].upper() in CI_SUCCESS_STATES
    ]
    repair_latency = _median_latency(history, successful_ci, CI_FAILURE_STATES)

    terminal_firsts: list[dict[str, Any]] = []
    for key in sorted({_entity_key(event) for event in history if event["entity_type"] == "ci"}):
        terminal = [
            event
            for event in history
            if _entity_key(event) == key
            and event["to_state"].upper() in CI_SUCCESS_STATES | CI_FAILURE_STATES
        ]
        if not terminal:
            continue
        first = min(terminal, key=lambda event: _parse_time(event["observed_at"], "observed_at"))
        first_time = _parse_time(first["observed_at"], "observed_at")
        if start_dt < first_time <= end_dt and (run_id is None or first["run_id"] == run_id):
            terminal_firsts.append(first)
    if terminal_firsts:
        first_pass_successes = sum(event["to_state"].upper() in CI_SUCCESS_STATES for event in terminal_firsts)
        first_pass_ci = _measured(
            first_pass_successes / len(terminal_firsts),
            len(terminal_firsts),
            (ref for event in terminal_firsts for ref in event["evidence_refs"]),
            unit="ratio",
        )
        first_pass_ci["first_pass_successes"] = first_pass_successes
        first_pass_ci["first_terminal_ci_runs"] = len(terminal_firsts)
    else:
        first_pass_ci = _unknown("no first terminal CI outcomes recorded in this period", unit="ratio")

    branch_events = [event for event in window if event["entity_type"] == "branch"]
    branch_history = [event for event in history if event["entity_type"] == "branch"]
    if branch_history:
        created = sum(
            event["to_state"].upper() in BRANCH_PRESENT_STATES
            and (event["from_state"] is None or str(event["from_state"]).upper() in BRANCH_DELETED_STATES)
            for event in branch_events
        )
        deleted = sum(event["to_state"].upper() in BRANCH_DELETED_STATES for event in branch_events)
        branch_net_delta = _measured(
            created - deleted,
            len(branch_events) if branch_events else 1,
            (ref for event in branch_events for ref in event["evidence_refs"]),
            unit="count",
        )
        branch_net_delta["created"] = created
        branch_net_delta["deleted"] = deleted

        latest: dict[tuple[str, str, str], dict[str, Any]] = {}
        for event in branch_history:
            key = _entity_key(event)
            if key not in latest or _parse_time(event["observed_at"], "observed_at") > _parse_time(latest[key]["observed_at"], "observed_at"):
                latest[key] = event
        debt = [event for event in latest.values() if event["to_state"].upper() in BRANCH_DEBT_STATES]
        branch_hygiene_debt = _measured(
            len(debt),
            len(latest),
            (ref for event in debt for ref in event["evidence_refs"]),
            unit="count",
        )
        branch_hygiene_debt["branches_observed"] = len(latest)
    else:
        branch_net_delta = _unknown("no branch telemetry has been recorded", unit="count")
        branch_hygiene_debt = _unknown("no branch telemetry has been recorded", unit="count")

    material = [event for event in window if event["transition_class"] in MATERIAL_CLASSES]
    human = [event for event in material if event["actor"] == "HUMAN" or event["transition_class"] == "HUMAN_INTERVENTION"]
    if material:
        human_intervention = _measured(
            len(human) / len(material),
            len(material),
            (ref for event in material for ref in event["evidence_refs"]),
            unit="ratio",
        )
        human_intervention["human_intervention_count"] = len(human)
        human_intervention["material_transition_count"] = len(material)
    else:
        human_intervention = _unknown("no material transitions recorded in this period", unit="ratio")

    return {
        "schema_version": SCHEMA_VERSION,
        "period": {"start": start, "end": end, "run_id": run_id},
        "event_count": len(window),
        "metrics": {
            "issue_advancement_rate": issue_advancement,
            "cycle_time_seconds": cycle_time,
            "repair_latency_seconds": repair_latency,
            "promotion_latency_seconds": promotion_latency,
            "first_pass_ci_rate": first_pass_ci,
            "branch_net_delta": branch_net_delta,
            "branch_hygiene_debt": branch_hygiene_debt,
            "human_intervention_rate": human_intervention,
        },
        "rules": [
            "product-flow reports exclude project-orchestrator control-plane events",
            "UNKNOWN is preserved when required transition pairs are absent",
            "first_pass_ci_rate is observational and does not replace the authoritative first_pass_preflight_rate target",
            "snapshots are projections; the append-only event ledger is the durable telemetry authority",
        ],
    }


def build_rolling_reports(events: list[dict[str, Any]], *, end: str) -> dict[str, Any]:
    end_dt = _parse_time(end, "end")
    periods = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
    return {
        label: build_flow_report(events, start=(end_dt - delta).isoformat(), end=end_dt.isoformat())
        for label, delta in periods.items()
    }


def build_run_snapshot(events: list[dict[str, Any]], *, run_id: str, generated_at: str) -> dict[str, Any]:
    if not isinstance(run_id, str) or not run_id.strip():
        raise KpiTelemetryError("run_id must be a non-empty string")
    end_dt = _parse_time(generated_at, "generated_at")
    normalized = [validate_event(event) for event in events]
    run_events = [event for event in normalized if event["run_id"] == run_id]
    if run_events:
        start_dt = min(_parse_time(event["observed_at"], "observed_at") for event in run_events) - timedelta(microseconds=1)
    else:
        start_dt = end_dt - timedelta(microseconds=1)
    return validate_snapshot(
        {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "generated_at": generated_at,
            "run_event_count": len(run_events),
            "run_metrics": build_flow_report(
                normalized,
                start=start_dt.isoformat(),
                end=end_dt.isoformat(),
                run_id=run_id,
            ),
            "rolling_reports": build_rolling_reports(normalized, end=generated_at),
        }
    )
