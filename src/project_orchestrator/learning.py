from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

ALLOWED_PROJECTS = {"portfolio", "knowledge-engine", "rocksmith", "everward", "voicestudio"}
ALLOWED_SOURCE_TYPES = {"ci_failure", "review_finding", "product_reality", "governance", "success_pattern"}
SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(password|api[_-]?key|secret|token)\s*[:=]\s*[^\s]+"),
]
REQUIRED_LESSON_FIELDS = {
    "project",
    "repository",
    "source_type",
    "source_ref",
    "symptom",
    "root_cause",
    "fix",
    "verification",
    "regression_protection",
    "residual_risk",
    "evidence_state",
}


class LearningMemoryError(ValueError):
    """Raised when a learning-memory record violates the sanitized contract."""


def _contains_secret(value: Any) -> bool:
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in SECRET_PATTERNS)
    if isinstance(value, list):
        return any(_contains_secret(item) for item in value)
    if isinstance(value, dict):
        return any(
            _contains_secret(key) or _contains_secret(item)
            for key, item in value.items()
        )
    return False


def _lesson_identity(lesson: dict[str, Any]) -> str:
    stable = {
        key: lesson[key]
        for key in sorted(REQUIRED_LESSON_FIELDS)
        if key != "verification"
    }
    stable["verification"] = lesson["verification"]
    encoded = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def validate_lesson(lesson: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(lesson, dict):
        raise LearningMemoryError("lesson must be an object")
    missing = sorted(REQUIRED_LESSON_FIELDS - set(lesson))
    if missing:
        raise LearningMemoryError(f"lesson missing required fields: {missing}")

    if lesson["project"] not in ALLOWED_PROJECTS:
        raise LearningMemoryError("lesson project is not in the portfolio")
    if lesson["source_type"] not in ALLOWED_SOURCE_TYPES:
        raise LearningMemoryError("unsupported source_type")
    if lesson["evidence_state"] != "VERIFIED":
        raise LearningMemoryError("only VERIFIED lessons may be persisted")

    for field in REQUIRED_LESSON_FIELDS - {"project", "source_type", "evidence_state"}:
        value = lesson[field]
        if not isinstance(value, str) or not value.strip():
            raise LearningMemoryError(f"{field} must be a non-empty string")

    forbidden_fields = {
        "raw_payload",
        "private_payload",
        "credentials",
        "environment",
        "secret",
        "token",
    }
    if forbidden_fields.intersection(lesson):
        raise LearningMemoryError("lesson contains a forbidden raw/private field")
    if _contains_secret(lesson):
        raise LearningMemoryError("lesson appears to contain secret material")

    normalized = {key: lesson[key] for key in REQUIRED_LESSON_FIELDS}
    normalized["lesson_id"] = _lesson_identity(normalized)
    return normalized


def validate_memory(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != 1:
        raise LearningMemoryError("learning-memory schema_version must be 1")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise LearningMemoryError("learning-memory entries must be a list")

    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    for raw in entries:
        lesson = validate_lesson(raw)
        lesson_id = raw.get("lesson_id", lesson["lesson_id"])
        if lesson_id != lesson["lesson_id"]:
            raise LearningMemoryError("lesson_id does not match verified content")
        if lesson_id in ids:
            raise LearningMemoryError(f"duplicate lesson_id: {lesson_id}")
        ids.add(lesson_id)
        lesson["lesson_id"] = lesson_id
        normalized.append(lesson)
    return {"schema_version": 1, "entries": normalized}


def load_memory(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_memory(payload)


def add_lesson(memory: dict[str, Any], lesson: dict[str, Any]) -> dict[str, Any]:
    normalized_memory = validate_memory(memory)
    normalized_lesson = validate_lesson(lesson)
    if any(
        entry["lesson_id"] == normalized_lesson["lesson_id"]
        for entry in normalized_memory["entries"]
    ):
        return normalized_memory
    return {
        "schema_version": 1,
        "entries": [*normalized_memory["entries"], normalized_lesson],
    }


def write_memory(path: str | Path, memory: dict[str, Any]) -> None:
    normalized = validate_memory(memory)
    Path(path).write_text(
        json.dumps(normalized, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
