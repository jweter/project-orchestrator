from __future__ import annotations

import copy

import pytest

from project_orchestrator.learning import validate_lesson
from project_orchestrator.skill_extraction import (
    SkillExtractionError,
    build_report,
    select_skills,
    validate_catalog,
)


def lesson() -> dict:
    return validate_lesson(
        {
            "project": "portfolio",
            "repository": "jweter/project-orchestrator",
            "source_type": "ci_failure",
            "source_ref": "preflight:34182868481",
            "symptom": "generic overlap fabricated risk",
            "root_cause": "generic structural tokens created weak overlap",
            "fix": "filter generic tokens and require informative evidence",
            "verification": "exact-head preflight passed",
            "regression_protection": "preserve NO_RELEVANT_VERIFIED_HISTORY without informative matches",
            "residual_risk": "new generic layout tokens may appear",
            "evidence_state": "VERIFIED",
        }
    )


def memory() -> dict:
    return {"schema_version": 1, "entries": [lesson()]}


def catalog() -> dict:
    return {
        "schema_version": 1,
        "skills": [
            {
                "skill_key": "historical-similarity-false-positive-guard",
                "project": "portfolio",
                "repository": "jweter/project-orchestrator",
                "status": "PILOT_READY",
                "authority": "ADVISORY_ONLY",
                "source_lesson_ids": [lesson()["lesson_id"]],
                "history_refs": ["commit:9675dab700fada6b6fa5c6a02f45b1261dbf7cc4"],
                "path_patterns": ["src/project_orchestrator/risk_predictor.py"],
                "task_triggers": ["risk predictor", "path similarity"],
                "problem": "generic overlap can fabricate risk",
                "prevention_rule": "require informative evidence",
                "verification_commands": ["python -m pytest -q tests/test_risk_predictor.py"],
                "residual_risk": "new generic tokens may appear",
                "effectiveness_state": "UNMEASURED",
                "product_reality_may_be_inferred": False,
            }
        ],
    }


def test_verified_history_skill_validates() -> None:
    result = validate_catalog(catalog(), memory())
    assert len(result["skills"]) == 1
    skill = result["skills"][0]
    assert len(skill["skill_id"]) == 20
    assert skill["authority"] == "ADVISORY_ONLY"
    assert skill["effectiveness_state"] == "UNMEASURED"


def test_unknown_lesson_fails_closed() -> None:
    payload = catalog()
    payload["skills"][0]["source_lesson_ids"] = ["missing"]
    with pytest.raises(SkillExtractionError, match="not present in verified memory"):
        validate_catalog(payload, memory())


def test_cross_repository_transfer_is_rejected() -> None:
    payload = catalog()
    payload["skills"][0]["repository"] = "jweter/Project-Everward"
    with pytest.raises(SkillExtractionError, match="silently transfer"):
        validate_catalog(payload, memory())


def test_non_exact_history_ref_is_rejected() -> None:
    payload = catalog()
    payload["skills"][0]["history_refs"] = ["commit:main"]
    with pytest.raises(SkillExtractionError, match="exact 40-character"):
        validate_catalog(payload, memory())


def test_product_reality_inference_is_rejected() -> None:
    payload = catalog()
    payload["skills"][0]["product_reality_may_be_inferred"] = True
    with pytest.raises(SkillExtractionError, match="Product Reality"):
        validate_catalog(payload, memory())


def test_path_match_selects_advisory_skill() -> None:
    result = select_skills(
        catalog(),
        memory(),
        repository="jweter/project-orchestrator",
        changed_paths=["src/project_orchestrator/risk_predictor.py"],
        task_summary="unrelated change",
    )
    assert result["selection_state"] == "ADVISORY_SKILLS_SELECTED"
    assert result["selected_skills"][0]["matched_paths"] == [
        "src/project_orchestrator/risk_predictor.py"
    ]


def test_task_trigger_selects_without_path_match() -> None:
    result = select_skills(
        catalog(),
        memory(),
        repository="jweter/project-orchestrator",
        changed_paths=["docs/notes.md"],
        task_summary="Repair the risk predictor path similarity logic",
    )
    assert result["selection_state"] == "ADVISORY_SKILLS_SELECTED"
    assert result["selected_skills"][0]["matched_task_triggers"] == [
        "path similarity",
        "risk predictor",
    ]


def test_no_fuzzy_or_cross_repo_match() -> None:
    result = select_skills(
        catalog(),
        memory(),
        repository="jweter/Project-Everward",
        changed_paths=["src/simulation/risk_predictor.cpp"],
        task_summary="similarity work",
    )
    assert result["selection_state"] == "NO_EXPLICIT_SKILL_MATCH"
    assert result["selected_skills"] == []


def test_skill_effectiveness_starts_unmeasured() -> None:
    report = build_report(catalog(), memory())
    assert report["skill_count"] == 1
    assert report["pilot_ready"] == 1
    assert report["effectiveness_state"] == "UNMEASURED"
    assert report["effectiveness_measured"] == 0


def test_content_change_invalidates_supplied_skill_id() -> None:
    normalized = validate_catalog(catalog(), memory())
    supplied = copy.deepcopy(catalog())
    supplied["skills"][0]["skill_id"] = normalized["skills"][0]["skill_id"]
    supplied["skills"][0]["prevention_rule"] = "weakened rule"
    with pytest.raises(SkillExtractionError, match="skill_id does not match"):
        validate_catalog(supplied, memory())
