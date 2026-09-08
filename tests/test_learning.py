import pytest

from project_orchestrator.learning import (
    LearningMemoryError,
    add_lesson,
    validate_lesson,
    validate_memory,
)


def lesson(**overrides):
    payload = {
        "project": "portfolio",
        "repository": "jweter/project-orchestrator",
        "source_type": "ci_failure",
        "source_ref": "project-orchestrator#3",
        "symptom": "A deterministic test failed.",
        "root_cause": "Fixture omitted normalized evidence metadata.",
        "fix": "Normalize evidence_kind before selection.",
        "verification": "Exact-head preflight passed.",
        "regression_protection": "Regression test covers the omitted field.",
        "residual_risk": "None identified in the bounded change.",
        "evidence_state": "VERIFIED",
    }
    payload.update(overrides)
    return payload


def test_verified_lesson_receives_stable_identity() -> None:
    first = validate_lesson(lesson())
    second = validate_lesson(lesson())
    assert first["lesson_id"] == second["lesson_id"]


def test_unverified_lesson_is_rejected() -> None:
    with pytest.raises(LearningMemoryError, match="only VERIFIED"):
        validate_lesson(lesson(evidence_state="PENDING"))


def test_secret_like_material_is_rejected() -> None:
    with pytest.raises(LearningMemoryError, match="secret material"):
        validate_lesson(lesson(fix="stored token=super-secret-value-here"))


def test_private_payload_fields_are_rejected() -> None:
    candidate = lesson()
    candidate["raw_payload"] = "private product reality evidence"
    with pytest.raises(LearningMemoryError, match="forbidden"):
        validate_lesson(candidate)


def test_add_lesson_is_idempotent() -> None:
    memory = {"schema_version": 1, "entries": []}
    once = add_lesson(memory, lesson())
    twice = add_lesson(once, lesson())
    assert len(twice["entries"]) == 1


def test_memory_rejects_duplicate_ids() -> None:
    normalized = validate_lesson(lesson())
    with pytest.raises(LearningMemoryError, match="duplicate lesson_id"):
        validate_memory(
            {
                "schema_version": 1,
                "entries": [normalized, normalized],
            }
        )


def test_verified_success_pattern_is_allowed() -> None:
    result = validate_lesson(lesson(source_type="success_pattern"))
    assert result["source_type"] == "success_pattern"
