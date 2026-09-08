from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .learning import _contains_secret, validate_memory

OUTCOMES = {
    "PREVENTED_FAILURE",
    "EARLIER_DETECTION",
    "SHORTENED_REPAIR",
    "SUCCESS_PATTERN_REUSED",
    "NO_MEASURABLE_EFFECT",
    "UNKNOWN",
}


class LearningEffectivenessError(ValueError):
    """Raised when a learning-application record violates the evidence contract."""


def _identity(record: dict[str, Any]) -> str:
    stable = {key: record[key] for key in sorted(record) if key != "application_id"}
    encoded = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def validate_application(record: dict[str, Any], memory: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise LearningEffectivenessError("learning application must be an object")
    required = {
        "lesson_id",
        "repository",
        "change_ref",
        "applied_prevention",
        "outcome",
        "verification",
        "evidence_state",
    }
    missing = required - record.keys()
    if missing:
        raise LearningEffectivenessError(
            "learning application missing fields: " + ", ".join(sorted(missing))
        )
    normalized_memory = validate_memory(memory)
    lesson_ids = {entry["lesson_id"] for entry in normalized_memory["entries"]}
    if record["lesson_id"] not in lesson_ids:
        raise LearningEffectivenessError("lesson_id is not present in verified learning memory")
    if record["evidence_state"] != "VERIFIED":
        raise LearningEffectivenessError("only VERIFIED learning applications may be persisted")
    if record["outcome"] not in OUTCOMES:
        raise LearningEffectivenessError("unsupported learning outcome")
    for field in ("repository", "change_ref", "applied_prevention", "verification"):
        if not isinstance(record[field], str) or not record[field].strip():
            raise LearningEffectivenessError(f"{field} must be a non-empty string")
    if _contains_secret(record):
        raise LearningEffectivenessError("learning application appears to contain secret material")

    normalized = {key: record[key] for key in required}
    normalized["application_id"] = _identity(normalized)
    supplied = record.get("application_id")
    if supplied is not None and supplied != normalized["application_id"]:
        raise LearningEffectivenessError("application_id does not match verified content")
    return normalized


def validate_store(payload: dict[str, Any], memory: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise LearningEffectivenessError("learning-effectiveness schema_version must be 1")
    applications = payload.get("applications")
    if not isinstance(applications, list):
        raise LearningEffectivenessError("applications must be a list")
    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    for raw in applications:
        item = validate_application(raw, memory)
        if item["application_id"] in ids:
            raise LearningEffectivenessError(f"duplicate application_id: {item['application_id']}")
        ids.add(item["application_id"])
        normalized.append(item)
    normalized.sort(key=lambda item: (item["repository"], item["change_ref"], item["application_id"]))
    return {"schema_version": 1, "applications": normalized}


def add_application(
    store: dict[str, Any], record: dict[str, Any], memory: dict[str, Any]
) -> dict[str, Any]:
    normalized_store = validate_store(store, memory)
    item = validate_application(record, memory)
    if any(existing["application_id"] == item["application_id"] for existing in normalized_store["applications"]):
        return normalized_store
    return validate_store(
        {"schema_version": 1, "applications": [*normalized_store["applications"], item]},
        memory,
    )


def build_report(store: dict[str, Any], memory: dict[str, Any]) -> dict[str, Any]:
    normalized_store = validate_store(store, memory)
    applications = normalized_store["applications"]
    by_outcome = {
        outcome: sum(1 for item in applications if item["outcome"] == outcome)
        for outcome in sorted(OUTCOMES)
    }
    effective = sum(
        by_outcome[outcome]
        for outcome in (
            "PREVENTED_FAILURE",
            "EARLIER_DETECTION",
            "SHORTENED_REPAIR",
            "SUCCESS_PATTERN_REUSED",
        )
    )
    measurable = effective + by_outcome["NO_MEASURABLE_EFFECT"]
    return {
        "schema_version": 1,
        "verified_applications": len(applications),
        "effective_applications": effective,
        "measurable_applications": measurable,
        "outcomes": by_outcome,
        "learning_effectiveness_rate": (
            {"status": "MEASURED", "value": effective / measurable}
            if measurable
            else {"status": "INSUFFICIENT_DATA"}
        ),
        "interpretation": (
            "A lesson is effective only when verified evidence shows prevention, earlier detection, shorter repair, or successful-pattern reuse."
        ),
    }


def load_store(path: str | Path, memory: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_store(payload, memory)


def write_store(path: str | Path, store: dict[str, Any], memory: dict[str, Any]) -> None:
    normalized = validate_store(store, memory)
    Path(path).write_text(
        json.dumps(normalized, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
