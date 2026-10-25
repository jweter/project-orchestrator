from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from .evolution_bridge import normalize_worker_result
from .grant_lifecycle import acquire_lifecycle_lock, release_lifecycle_lock
from .state_io import atomic_json

PACKET_STATES = {
    "CANDIDATE",
    "SELECTED",
    "RESERVED",
    "DISPATCHED",
    "IMPLEMENTING",
    "WAITING_FOR_SUPERVISOR",
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


def _digest(value: dict[str, Any]) -> str:
    """Deterministic corruption checksum, NOT an authentication signature."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _event_id(event: dict[str, Any]) -> str:
    stable = {key: event[key] for key in sorted(event) if key != "event_id"}
    return _digest(stable)


def _sealed(ledger: dict[str, Any]) -> dict[str, Any]:
    ledger["seal_sha256"] = _digest({key: value for key, value in ledger.items() if key != "seal_sha256"})
    return ledger


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


CANON_PLAN_FIELDS = {
    "vision_ref", "roadmap_ref", "architecture_ref", "current_state_ref",
    "dependency_evidence_ref", "source_head_sha", "objective",
    "acceptance_criteria", "allowed_paths", "forbidden_paths",
    "verification_commands", "expected_artifacts", "run_budget_minutes",
    "handoff_instructions", "escalation_policy",
}


def validate_canonical_slice_plan(packet: dict[str, Any]) -> dict[str, Any]:
    """Require complete bounded planning evidence before supervisor issuance.

    References identify material the planner must actually inspect; these
    fields cannot themselves prove a document was read or is up-to-date.
    Live ownership and authority must be independently checked at dispatch.
    """
    work = validate_packet(packet)
    plan = packet.get("planning")
    if not isinstance(plan, dict) or CANON_PLAN_FIELDS - plan.keys():
        raise WorkerTransportError("canonical slice requires complete planning evidence")
    for field in (
        "vision_ref", "roadmap_ref", "architecture_ref", "current_state_ref",
        "dependency_evidence_ref", "objective",
    ):
        _safe_handoff_text(plan[field], field)
    _safe_handoff_text(plan["handoff_instructions"], "handoff_instructions", max_length=8000)
    if plan["source_head_sha"] != work["base_sha"]:
        raise WorkerTransportError("canonical plan must bind the exact dispatched base")
    if plan["objective"] != work["task"]:
        raise WorkerTransportError("canonical objective differs from dispatched worker task")
    if (
        type(plan["run_budget_minutes"]) is not int
        or not 1 <= plan["run_budget_minutes"] <= 120
    ):
        raise WorkerTransportError("canonical slice must fit one bounded worker run")
    if plan["escalation_policy"] != "ASK_SUPERVISOR_AND_PAUSE":
        raise WorkerTransportError("canonical slice must require supervisor escalation")
    for field in ("acceptance_criteria", "allowed_paths", "verification_commands", "expected_artifacts"):
        rows = plan.get(field)
        if not isinstance(rows, list) or not rows or len(rows) > 32:
            raise WorkerTransportError(f"{field} must be a bounded non-empty list")
        for value in rows:
            _safe_handoff_text(value, field)
    forbidden = plan["forbidden_paths"]
    if not isinstance(forbidden, list) or len(forbidden) > 32:
        raise WorkerTransportError("forbidden_paths must be a bounded list")
    for item in forbidden:
        _safe_handoff_text(item, "forbidden_paths")
    if plan["allowed_paths"] != work["paths"] or plan["verification_commands"] != work["verification"]:
        raise WorkerTransportError("planning scope or verification differs from issued packet")
    if any(
        item.startswith("/") or ".." in item.replace(chr(92), "/").split("/")
        for item in [*plan["allowed_paths"], *forbidden]
    ):
        raise WorkerTransportError("canonical plan contains unsafe path scope")
    return {**work, "planning": dict(plan)}


def dispatch_canonical_slice(
    ledger: dict[str, Any], packet: dict[str, Any], *, transport: str,
) -> dict[str, Any]:
    """Issue a single-run planned handoff using the existing transport ledger.

    The authority-grant and live owner checks remain prerequisites of the
    caller; this pure function does not start processes or approve code.
    """
    planned = validate_canonical_slice_plan(packet)
    return dispatch_packet(ledger, planned, transport=transport)


def empty_ledger() -> dict[str, Any]:
    """A sealed transport ledger, incompatible with unaudited v1 projections."""
    return _sealed({"schema_version": 2, "revision": 0, "packets": {}, "events": []})


def validate_ledger(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 2:
        raise WorkerTransportError("ledger requires verified schema_version 2")
    if set(payload) != {"schema_version", "revision", "packets", "events", "seal_sha256"}:
        raise WorkerTransportError("ledger envelope missing integrity fields")
    if not isinstance(payload.get("packets"), dict) or not isinstance(payload.get("events"), list):
        raise WorkerTransportError("ledger packets/events have invalid shape")
    packets, events = payload["packets"], payload["events"]
    revision = payload.get("revision")
    if type(revision) is not int or revision != len(events):
        raise WorkerTransportError("ledger revision does not match append-only history")
    if payload.get("seal_sha256") != _digest({key: value for key, value in payload.items() if key != "seal_sha256"}):
        raise WorkerTransportError("ledger corruption checksum mismatch")

    # Deep copies prevent a caller from mutating a previously validated snapshot.
    normalized = deepcopy(payload)
    previous = "GENESIS"
    latest: dict[str, str] = {}
    for sequence, event in enumerate(normalized["events"], 1):
        if not isinstance(event, dict) or event.get("seq") != sequence:
            raise WorkerTransportError("ledger event sequence changed")
        if event.get("previous_event_id") != previous or event.get("event_id") != _event_id(event):
            raise WorkerTransportError("ledger event chain is broken")
        packet_id = event.get("packet_id")
        record = packets.get(packet_id)
        if not isinstance(record, dict):
            raise WorkerTransportError("ledger event refers to unknown packet")
        if event.get("repository") not in {None, record.get("repository")}:
            raise WorkerTransportError("ledger event repository mismatch")
        if event.get("base_sha") not in {None, record.get("base_sha")}:
            raise WorkerTransportError("ledger event base mismatch")
        state_after = event.get("state_after")
        if state_after not in PACKET_STATES:
            raise WorkerTransportError("ledger event has no valid state projection")
        latest[packet_id] = state_after
        previous = event["event_id"]

    if set(packets) != set(latest) and packets:
        raise WorkerTransportError("ledger packet lacks dispatch history")
    for packet_id, record in normalized["packets"].items():
        if not isinstance(record, dict) or record.get("packet_id") != packet_id:
            raise WorkerTransportError("ledger packet identity mismatch")
        if record.get("state") not in PACKET_STATES or latest.get(packet_id) != record["state"]:
            raise WorkerTransportError("ledger packet state disagrees with history")
        if record.get("merge_authority") is not False or record.get("product_reality_authority") is not False:
            raise WorkerTransportError("ledger packet cannot grant promotion authority")
        required = REQUIRED_PACKET_FIELDS - set(record)
        if required:
            raise WorkerTransportError("ledger packet missing immutable identity")
        if not isinstance(record.get("paths"), list):
            raise WorkerTransportError("ledger packet paths are not a list")
        plan = record.get("planning")
        if plan is not None and (
            not isinstance(plan, dict)
            or record.get("base_sha") != plan.get("source_head_sha")
        ):
            raise WorkerTransportError("ledger packet base mismatch with plan")
    return normalized


def _append_event(ledger: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """Finalize a validated-and-mutated ledger snapshot with one new event."""
    material = dict(event)
    packet_id = material["packet_id"]
    material["seq"] = len(ledger["events"]) + 1
    material["previous_event_id"] = ledger["events"][-1]["event_id"] if ledger["events"] else "GENESIS"
    material["state_after"] = ledger["packets"][packet_id]["state"]
    material["event_id"] = _event_id(material)
    ledger["events"].append(material)
    ledger["revision"] = len(ledger["events"])
    return _sealed(ledger)


def dispatch_packet(ledger: dict[str, Any], packet: dict[str, Any], *, transport: str) -> dict[str, Any]:
    normalized = validate_ledger(ledger)
    work = validate_packet(packet)
    if transport not in {"github_event", "n8n", "local_idle_worker"}:
        raise WorkerTransportError("unsupported transport")
    existing = normalized["packets"].get(work["packet_id"])
    if existing and existing["state"] in TERMINAL_STATES:
        raise WorkerTransportError("terminal packet cannot be redispatched")
    if existing and existing.get("supervisor_question"):
        raise WorkerTransportError("supervisor exchange requires an explicit new approved packet")
    if existing and existing.get("base_sha") != work["base_sha"]:
        raise WorkerTransportError("packet base changed; reconcile before redispatch")
    if existing and (
        any(existing.get(field) != work.get(field) for field in REQUIRED_PACKET_FIELDS)
        or existing.get("planning") != work.get("planning")
        or existing.get("paths") != work.get("paths")
    ):
        raise WorkerTransportError("issued packet changed; require a new approved packet")
    if existing:
        if existing["state"] == "DISPATCHED" and existing.get("transport") == transport:
            return normalized  # idempotent request, never rewrites state or provenance
        raise WorkerTransportError("redispatch requires new ownership and a new packet")
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


_QUESTION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SECRET = re.compile(
    r"(?i)\b(?:password|api[_-]?key|secret|access[_-]?token)\s*[:=]\s*\S+"
    r"|gh[pousr]_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}"
)
QUESTION_DECISIONS = {"CONTINUE_WITHIN_SCOPE", "REPLAN_REQUIRED", "PARK_SLICE"}


def _safe_handoff_text(value: str, field: str, *, max_length: int = 1200) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > max_length
        or any(ord(char) < 32 and char not in "\n\t" for char in value)
        or _SECRET.search(value)
    ):
        raise WorkerTransportError(f"{field} must be bounded, sanitized non-empty text")
    return value.strip()


def request_supervisor_guidance(
    ledger: dict[str, Any], packet_id: str, *,
    question_id: str, worker_id: str, summary: str, evidence_ref: str,
    expected_base_sha: str,
) -> dict[str, Any]:
    """Pause one issued slice without changing its work authority or creating a queue.

    A worker can communicate an obstacle but cannot widen paths, edit product
    intent, auto-answer its own question, or self-approve completion.
    """
    normalized = validate_ledger(ledger)
    record = normalized["packets"].get(packet_id)
    if record is None or record["state"] not in {"DISPATCHED", "IMPLEMENTING", "WAITING_FOR_SUPERVISOR"}:
        raise WorkerTransportError("question requires an active dispatched packet")
    if record["base_sha"] != expected_base_sha:
        raise WorkerTransportError("question base does not match the accepted dispatch")
    if not isinstance(question_id, str) or not _QUESTION_ID.fullmatch(question_id):
        raise WorkerTransportError("question_id must be a bounded stable identifier")
    worker_id = _safe_handoff_text(worker_id, "worker_id")
    summary = _safe_handoff_text(summary, "summary")
    evidence_ref = _safe_handoff_text(evidence_ref, "evidence_ref")
    if record.get("worker_id") not in {None, worker_id}:
        raise WorkerTransportError("question worker identity mismatch")
    existing = record.get("supervisor_question")
    question = {
        "question_id": question_id, "worker_id": worker_id,
        "summary": summary, "evidence_ref": evidence_ref,
        "expected_base_sha": expected_base_sha,
        "status": "PENDING",
    }
    if existing is not None:
        if existing == question and record["state"] == "WAITING_FOR_SUPERVISOR":
            return normalized  # safe replay
        history = list(record.get("supervisor_history", []))
        if (
            existing.get("status") != "ANSWERED"
            or existing.get("decision") != "CONTINUE_WITHIN_SCOPE"
            or len(history) >= 8
            or question_id in {row["question_id"] for row in [*history, existing]}
        ):
            raise WorkerTransportError("unresolved or duplicate supervisor question")
        record["supervisor_history"] = [*history, existing]
    previous = record["state"]
    record.update({
        "worker_id": worker_id,
        "resume_state": previous,
        "supervisor_question": question,
        "state": "WAITING_FOR_SUPERVISOR",
    })
    normalized["packets"][packet_id] = record
    return _append_event(normalized, {
        "type": "WORKER_QUESTION", "packet_id": packet_id,
        "repository": record["repository"], "base_sha": record["base_sha"],
        "question_id": question_id, "worker_id": worker_id,
        "evidence_ref": evidence_ref, "summary": summary,
    })


def respond_to_supervisor_question(
    ledger: dict[str, Any], packet_id: str, *,
    question_id: str, decision: str, guidance: str,
    expected_base_sha: str,
) -> dict[str, Any]:
    """Record a supervisor answer; CONTINUE never broadens the original grant."""
    normalized = validate_ledger(ledger)
    record = normalized["packets"].get(packet_id)
    if record is None or record["state"] != "WAITING_FOR_SUPERVISOR":
        raise WorkerTransportError("no pending supervisor question for packet")
    question = record.get("supervisor_question")
    if (
        not isinstance(question, dict) or question.get("status") != "PENDING"
        or question.get("question_id") != question_id
        or question.get("expected_base_sha") != expected_base_sha
        or record["base_sha"] != expected_base_sha
    ):
        raise WorkerTransportError("supervisor response question identity mismatch")
    if decision not in QUESTION_DECISIONS:
        raise WorkerTransportError("unsupported supervisor decision")
    guidance = _safe_handoff_text(guidance, "guidance")
    question = {**question, "status": "ANSWERED", "decision": decision, "guidance": guidance}
    record["supervisor_question"] = question
    record["state"] = (
        record["resume_state"] if decision == "CONTINUE_WITHIN_SCOPE"
        else "RECONCILE_REQUIRED" if decision == "REPLAN_REQUIRED"
        else "PARKED"
    )
    record.pop("resume_state", None)
    normalized["packets"][packet_id] = record
    return _append_event(normalized, {
        "type": "SUPERVISOR_RESPONSE", "packet_id": packet_id,
        "repository": record["repository"], "base_sha": record["base_sha"],
        "question_id": question_id, "decision": decision, "guidance": guidance,
    })


def read_supervisor_response(
    ledger: dict[str, Any], packet_id: str, *, question_id: str,
    expected_base_sha: str,
) -> dict[str, Any]:
    """Read-only worker polling; no auto-resume, merge, or grant mutation."""
    record = validate_ledger(ledger)["packets"].get(packet_id)
    if record is None or record["base_sha"] != expected_base_sha:
        raise WorkerTransportError("supervisor response packet identity mismatch")
    question = record.get("supervisor_question")
    if not isinstance(question, dict) or question.get("question_id") != question_id:
        raise WorkerTransportError("supervisor response question identity mismatch")
    return dict(question)


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
    if record["state"] == "WAITING_FOR_SUPERVISOR" or record.get("supervisor_question", {}).get("decision") in {"PARK_SLICE", "REPLAN_REQUIRED"}:
        raise WorkerTransportError("supervisor question must be resolved before worker result")

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
    if record["state"] == "WAITING_FOR_SUPERVISOR" or record.get("supervisor_question", {}).get("decision") in {"PARK_SLICE", "REPLAN_REQUIRED"}:
        raise WorkerTransportError("supervisor question prevents arbitrary state transitions")
    if exact_head_sha is not None:
        if len(exact_head_sha) != 40 or any(c not in "0123456789abcdef" for c in exact_head_sha):
            raise WorkerTransportError("exact_head_sha must be a lowercase 40-character SHA")
        record["exact_head_sha"] = exact_head_sha
    current_event = normalized["events"][-1] if normalized["events"] else {}
    if (
        record["state"] == state and current_event.get("type") == "STATE_TRANSITION"
        and current_event.get("packet_id") == packet_id
        and current_event.get("state") == state
        and current_event.get("exact_head_sha") == exact_head_sha
        and current_event.get("evidence_ref") == evidence_ref
    ):
        return normalized
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
    return [
        dict(record) for _, record in sorted(normalized["packets"].items())
        if record["state"] in allowed
        and record.get("supervisor_question", {}).get("decision") not in {"PARK_SLICE", "REPLAN_REQUIRED"}
    ]


def load_ledger(path: str | Path) -> dict[str, Any]:
    return validate_ledger(json.loads(Path(path).read_text(encoding="utf-8")))


def write_ledger(
    path: str | Path, ledger: dict[str, Any], *, expected_revision: int | None = None,
) -> None:
    """Atomic compare-and-swap on the EXISTING worker ledger, not a second queue.

    The kernel lock serializes cooperating processes, and atomic_json keeps the
    previous complete file intact when publication fails. A caller updating an
    existing ledger must supply the revision it read; stale writers are rejected.
    """
    destination = Path(path)
    normalized = validate_ledger(ledger)
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle = acquire_lifecycle_lock(destination)
    try:
        if destination.exists():
            current = load_ledger(destination)
            if expected_revision is None or expected_revision != current["revision"]:
                raise WorkerTransportError("LEDGER_REVISION_CONFLICT")
            if current == normalized:
                return
            if (
                normalized["revision"] <= current["revision"]
                or normalized["events"][:len(current["events"])] != current["events"]
            ):
                raise WorkerTransportError("LEDGER_HISTORY_CONFLICT")
        elif expected_revision not in {None, 0}:
            raise WorkerTransportError("LEDGER_REVISION_CONFLICT")
        atomic_json(destination, normalized)
    finally:
        release_lifecycle_lock(handle)
