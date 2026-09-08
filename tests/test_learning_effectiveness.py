import pytest

from project_orchestrator.learning import add_lesson
from project_orchestrator.learning_effectiveness import (
    LearningEffectivenessError,
    add_application,
    build_report,
    validate_application,
)


def memory():
    base = {"schema_version": 1, "entries": []}
    return add_lesson(
        base,
        {
            "project": "portfolio",
            "repository": "jweter/project-orchestrator",
            "source_type": "success_pattern",
            "source_ref": "project-orchestrator#31",
            "symptom": "Area-specific learning retrieval reached GREEN without repair cycles.",
            "root_cause": "The change reused existing verified promotion and learning patterns before mutation.",
            "fix": "Reuse the same branch-first, exact-head verification pattern.",
            "verification": "Exact-head preflight and fresh PR CI passed.",
            "regression_protection": "Retrieve relevant verified learning before edits and preserve branch-first promotion.",
            "residual_risk": "A successful pattern can become stale if repository authority changes.",
            "evidence_state": "VERIFIED",
        },
    )


def record(lesson_id, **overrides):
    payload = {
        "lesson_id": lesson_id,
        "repository": "jweter/project-orchestrator",
        "change_ref": "project-orchestrator#34",
        "applied_prevention": "branch-first promotion and exact-head verification",
        "outcome": "SUCCESS_PATTERN_REUSED",
        "verification": "exact-head preflight and fresh CI passed without repeat governance failure",
        "evidence_state": "VERIFIED",
    }
    payload.update(overrides)
    return payload


def test_success_pattern_is_persistable_learning_memory():
    mem = memory()
    assert mem["entries"][0]["source_type"] == "success_pattern"


def test_verified_application_references_existing_lesson():
    mem = memory()
    lesson_id = mem["entries"][0]["lesson_id"]
    item = validate_application(record(lesson_id), mem)
    assert item["application_id"]
    assert item["outcome"] == "SUCCESS_PATTERN_REUSED"


def test_unknown_lesson_fails_closed():
    with pytest.raises(LearningEffectivenessError, match="not present"):
        validate_application(record("missing"), memory())


def test_unverified_application_is_rejected():
    mem = memory()
    with pytest.raises(LearningEffectivenessError, match="only VERIFIED"):
        validate_application(
            record(mem["entries"][0]["lesson_id"], evidence_state="PENDING"),
            mem,
        )


def test_effectiveness_report_counts_verified_effect():
    mem = memory()
    lesson_id = mem["entries"][0]["lesson_id"]
    store = add_application(
        {"schema_version": 1, "applications": []},
        record(lesson_id),
        mem,
    )
    report = build_report(store, mem)
    assert report["verified_applications"] == 1
    assert report["effective_applications"] == 1
    assert report["learning_effectiveness_rate"]["value"] == 1.0


def test_unknown_outcome_does_not_fake_effectiveness():
    mem = memory()
    lesson_id = mem["entries"][0]["lesson_id"]
    store = add_application(
        {"schema_version": 1, "applications": []},
        record(lesson_id, outcome="UNKNOWN"),
        mem,
    )
    report = build_report(store, mem)
    assert report["learning_effectiveness_rate"]["status"] == "INSUFFICIENT_DATA"
