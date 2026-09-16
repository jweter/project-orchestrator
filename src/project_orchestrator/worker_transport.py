from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .evolution_bridge import normalize_worker_result

PACKET_STATES = {
    "CANDIDATE",
    "SELECTED",
    "RESERVED",
    "DISPATCHED",
    "IMPLEMENTING",
    "PREFLIGHT_UNVERIFIED",
    "PREFLIGHT_GREEN",
    "PR_OPEN",
    "CI_PENDING",
    "GREEN",
    "RECONCILE_REQUIRED",
    "PROMOTION_READY",
    "MERGED",
    "LEARNING_EXTRACTED",
    "BLOCKED",
    "PARKED",
    "CANCELLED",
}
TERMINAL_STATES = {"MERGED", "LEARNING_EXTRACTED", "CANCELLED"}
REQUIRED_PACKET_FIELDS = {"packet_id", "project", "repository", "source_ref", "base_sha", "branch", "task"}


class WorkerTransportError(ValueError):
    pass


def _event_id(event: dict[str, Any]) -> str:
    stable = {key: event[key] for key in sorted(event) if key not in {"event_id"}}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]


def validate_packet(packet: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(packet, dict):
        raise WorkerTransportError("packet must be an object")
    missing = sorted(REQUIRED_PACKET_FIELDS - packet.keys())
    if missing:
        raise WorkerTransportError(f"packet missing required fields: {missing}")
    for field in REQUIRED_PACKET_FIELDS:
        if not isinstance(packet[field], str) or not packet[field].strip():
            raise WorkerTransportError(f"{field} must be a non-empty string")
    if len(packet["base_sha"]) != 40 or any(c not in "0123456789abcdef" for c in packet["base_sha"]):
        raise WorkerTransportError("base_sha must be a lowercase 40-character SHA")
    if packet.get("merge_authority") is True or packet.get("product_reality_authority") is True:
        raise WorkerTransportError("worker packet may not grant merge or Product Reality authority")
    normalized = dict(packet)
    normalized["merge_authority"] = False
    normalized["product_reality_authority"] = False
    normalized["paths"] = sorted(set(packet.get("paths", [])))
    normalized["verification"] = list(packet.get("verification", []))
    normalized["capabilities_to_reuse"] = sorted(set(packet.get("capabilities_to_reuse", [])))
    return normalized


def empty_ledger() -> dict[str, Any]:
    return {"schema_version": 1, "packets": {}, "events": []}


def validate_ledger(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise WorkerTransportError("ledger schema_version must equal 1")
    packets = payload.get("packets")
    events = payload.get("events")
    if not isinstance(packets, dict) or not isinstance(events, list):
        raise WorkerTransportError("ledger packets/events have invalid shape")
    normalized_packets: dict[str, Any] = {}
    for packet_id, record in packets.items():
        if not isinstance(record, dict) or record.get("packet_id") != packet_id:
            raise WorkerTransportError("ledger packet identity mismatch")
        if record.get("state") not in PACKET_STATES:
            raise WorkerTransportError("ledger packet has unsupported state")
        normalized_packets[packet_id] = dict(record)
    normalized_events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in events:
        if not isinstance(raw, dict) or raw.get("event_id") != _event_id(raw):
            raise WorkerTransportError("ledger event_id does not match event content")
        if raw["event_id"] in seen:
            raise WorkerTransportError("duplicate ledger event")
        seen.add(raw["event_id"])
        normalized_events.append(dict(raw))
    return {"schema_version": 1, "packets": normalized_packets, "events": normalized_events}


def _append_event(ledger: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    normalized = validate_ledger(ledger)
    material = dict(event)
    material["event_id"] = _event_id(material)
    if any(existing["event_id"] == material["event_id"] for existing in normalized["events"]):
        return normalized
    normalized["events"].append(material)
    return normalized


def dispatch_packet(ledger: dict[str, Any], packet: dict[str, Any], *, transport: str) -> dict[str, Any]:
    normalized = validate_ledger(ledger)
    work = validate_packet(packet)
    if transport not in {"github_event", "n8n", "local_idle_worker"}:
        raise WorkerTransportError("unsupported transport")
    existing = normalized["packets"].get(work["packet_id"])
    if existing and existing["state"] in TERMINAL_STATES:
        raise WorkerTransportError("terminal packet cannot be redispatched")
    if existing and existing.get("base_sha") != work["base_sha"]:
        raise WorkerTransportError("packet base changed; reconcile before redispatch")
    normalized["packets"][work["packet_id"]] = {
        **work,
        "state": "DISPATCHED",
        "transport": transport,
        "exact_head_sha": existing.get("exact_head_sha") if existing else None,
    }
    return _append_event(normalized, {
        "type": "DISPATCHED",
        "packet_id": work["packet_id"],
        "repository": work["repository"],
        "base_sha": work["base_sha"],
        "transport": transport,
    })


def record_worker_result(ledger: dict[str, Any], packet_id: str, worker_result: dict[str, Any]) -> dict[str, Any]:
    normalized = validate_ledger(ledger)
    record = normalized["packets"].get(packet_id)
    if not record:
        raise WorkerTransportError("worker result has no dispatched packet")
    result = normalize_worker_result(worker_result)
    if result["repository"] != record["repository"] or result["project"] != record["project"]:
        raise WorkerTransportError("worker result project/repository mismatch")
    if result.get("branch") not in {None, record["branch"]}:
        raise WorkerTransportError("worker result branch mismatch")
    if record["state"] in TERMINAL_STATES:
        raise WorkerTransportError("terminal packet cannot accept worker result")

    state = {
        "PASS": "PREFLIGHT_GREEN",
        "FAIL": "BLOCKED",
        "REVIEW_REQUIRED": "BLOCKED",
        "PRODUCT_REALITY_REQUIRED": "PARKED",
        "ENVIRONMENT_FAILURE": "PARKED",
    }[result["status"]]
    record = {**record, "state": state, "exact_head_sha": result["head_sha"], "worker_status": result["status"], "evidence_ref": result["evidence_ref"]}
    normalized["packets"][packet_id] = record
    return _append_event(normalized, {
        "type": "WORKER_RESULT",
        "packet_id": packet_id,
        "repository": record["repository"],
        "exact_head_sha": result["head_sha"],
        "status": result["status"],
        "evidence_ref": result["evidence_ref"],
    })


def transition_packet(ledger: dict[str, Any], packet_id: str, *, state: str, exact_head_sha: str | None = None, evidence_ref: str | None = None) -> dict[str, Any]:
    normalized = validate_ledger(ledger)
    if state not in PACKET_STATES:
        raise WorkerTransportError("unsupported packet state")
    record = normalized["packets"].get(packet_id)
    if not record:
        raise WorkerTransportError("unknown packet")
    if record["state"] in TERMINAL_STATES and state != record["state"]:
        raise WorkerTransportError("terminal packet cannot transition")
    if exact_head_sha is not None:
        if len(exact_head_sha) != 40 or any(c not in "0123456789abcdef" for c in exact_head_sha):
            raise WorkerTransportError("exact_head_sha must be a lowercase 40-character SHA")
        record["exact_head_sha"] = exact_head_sha
    record["state"] = state
    normalized["packets"][packet_id] = record
    event = {"type": "STATE_TRANSITION", "packet_id": packet_id, "state": state}
    if exact_head_sha is not None:
        event["exact_head_sha"] = exact_head_sha
    if evidence_ref:
        event["evidence_ref"] = evidence_ref
    return _append_event(normalized, event)


def requeueable_packets(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    normalized = validate_ledger(ledger)
    allowed = {"BLOCKED", "PARKED", "RECONCILE_REQUIRED", "PREFLIGHT_UNVERIFIED"}
    return [dict(record) for _, record in sorted(normalized["packets"].items()) if record["state"] in allowed]


def load_ledger(path: str | Path) -> dict[str, Any]:
    return validate_ledger(json.loads(Path(path).read_text(encoding="utf-8")))


def write_ledger(path: str | Path, ledger: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(validate_ledger(ledger), indent=2, sort_keys=True) + "\n", encoding="utf-8")
