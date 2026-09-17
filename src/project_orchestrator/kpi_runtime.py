from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .kpi_telemetry import (
    KpiTelemetryError,
    append_event,
    append_snapshot,
    build_run_snapshot,
    load_events,
    load_snapshots,
    validate_event,
)

RUNTIME_SCHEMA_VERSION = 1
EVENT_KIND = "event"
FINALIZE_KIND = "finalize"


class KpiRuntimeError(ValueError):
    """Raised when runtime telemetry cannot be durably persisted as declared."""


def _require_exact_keys(raw: dict[str, Any], expected: set[str], *, label: str) -> None:
    actual = set(raw)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise KpiRuntimeError(f"{label} fields mismatch: missing={missing}, extra={extra}")


def validate_inbox_payload(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise KpiRuntimeError("telemetry inbox payload must be an object")
    if raw.get("schema_version") != RUNTIME_SCHEMA_VERSION:
        raise KpiRuntimeError("telemetry inbox schema_version must be 1")

    kind = raw.get("kind")
    if kind == EVENT_KIND:
        _require_exact_keys(raw, {"schema_version", "kind", "event"}, label="event payload")
        try:
            event = validate_event(raw["event"])
        except KpiTelemetryError as exc:
            raise KpiRuntimeError(str(exc)) from exc
        return {"schema_version": RUNTIME_SCHEMA_VERSION, "kind": EVENT_KIND, "event": event}

    if kind == FINALIZE_KIND:
        _require_exact_keys(
            raw,
            {"schema_version", "kind", "run_id", "generated_at"},
            label="finalize payload",
        )
        run_id = raw["run_id"]
        generated_at = raw["generated_at"]
        if not isinstance(run_id, str) or not run_id.strip():
            raise KpiRuntimeError("finalize run_id must be a non-empty string")
        if not isinstance(generated_at, str) or not generated_at.strip():
            raise KpiRuntimeError("finalize generated_at must be a non-empty ISO-8601 string")
        return {
            "schema_version": RUNTIME_SCHEMA_VERSION,
            "kind": FINALIZE_KIND,
            "run_id": run_id.strip(),
            "generated_at": generated_at,
        }

    raise KpiRuntimeError(f"unsupported telemetry inbox kind: {kind!r}")


def process_inbox_payload(
    raw: Any,
    *,
    event_ledger: str | Path,
    snapshot_ledger: str | Path,
) -> dict[str, Any]:
    """Persist one immutable runtime telemetry payload and verify the durable result.

    Event payloads are persisted immediately. Finalization payloads build exactly
    one run snapshot from the events already present in the durable event ledger.
    A conflicting finalization for the same run fails closed instead of silently
    accepting stale or partial evidence.
    """
    payload = validate_inbox_payload(raw)
    event_path = Path(event_ledger)
    snapshot_path = Path(snapshot_ledger)

    if payload["kind"] == EVENT_KIND:
        event = payload["event"]
        stored = append_event(event_path, event)
        persisted = {item["event_id"]: item for item in load_events(event_path)}
        durable = persisted.get(stored["event_id"])
        if durable != stored:
            raise KpiRuntimeError("event write could not be verified in the durable ledger")
        return {
            "schema_version": RUNTIME_SCHEMA_VERSION,
            "kind": EVENT_KIND,
            "status": "PERSISTED",
            "run_id": stored["run_id"],
            "event_id": stored["event_id"],
            "event_ledger_count": len(persisted),
        }

    events = load_events(event_path)
    candidate = build_run_snapshot(
        events,
        run_id=payload["run_id"],
        generated_at=payload["generated_at"],
    )
    stored = append_snapshot(snapshot_path, candidate)
    if stored != candidate:
        raise KpiRuntimeError(
            "run snapshot conflicts with an existing durable snapshot for this run_id"
        )
    snapshots = {item["run_id"]: item for item in load_snapshots(snapshot_path)}
    durable = snapshots.get(candidate["run_id"])
    if durable != candidate:
        raise KpiRuntimeError("run snapshot write could not be verified in the durable ledger")
    return {
        "schema_version": RUNTIME_SCHEMA_VERSION,
        "kind": FINALIZE_KIND,
        "status": "PERSISTED",
        "run_id": candidate["run_id"],
        "run_event_count": candidate["run_event_count"],
        "snapshot_ledger_count": len(snapshots),
    }


def process_inbox_file(
    path: str | Path,
    *,
    event_ledger: str | Path,
    snapshot_ledger: str | Path,
) -> dict[str, Any]:
    source = Path(path)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KpiRuntimeError(f"unable to read telemetry inbox payload {source}: {exc}") from exc
    return process_inbox_payload(
        raw,
        event_ledger=event_ledger,
        snapshot_ledger=snapshot_ledger,
    )


def persistence_health(
    *,
    event_ledger: str | Path,
    snapshot_ledger: str | Path,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Return fail-closed persistence health without inventing missing evidence."""
    try:
        events = load_events(event_ledger)
        snapshots = load_snapshots(snapshot_ledger)
    except (KpiTelemetryError, OSError) as exc:
        return {"status": "FAILED", "reason": str(exc)}

    result: dict[str, Any] = {
        "status": "HEALTHY",
        "event_count": len(events),
        "snapshot_count": len(snapshots),
    }
    if run_id is None:
        if not events and not snapshots:
            return {
                **result,
                "status": "UNKNOWN",
                "reason": "durable KPI ledgers contain no runtime evidence",
            }
        return result

    matching_events = [event for event in events if event["run_id"] == run_id]
    matching_snapshots = [snapshot for snapshot in snapshots if snapshot["run_id"] == run_id]
    if len(matching_snapshots) != 1:
        return {
            **result,
            "status": "FAILED",
            "run_id": run_id,
            "run_event_count": len(matching_events),
            "reason": f"expected exactly one durable run snapshot; found {len(matching_snapshots)}",
        }
    snapshot = matching_snapshots[0]
    if snapshot["run_event_count"] != len(matching_events):
        return {
            **result,
            "status": "FAILED",
            "run_id": run_id,
            "run_event_count": len(matching_events),
            "reason": "durable run snapshot event count does not match the event ledger",
        }
    return {
        **result,
        "run_id": run_id,
        "run_event_count": len(matching_events),
        "snapshot_persisted": True,
    }
