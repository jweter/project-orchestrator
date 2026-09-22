import pytest

from project_orchestrator.candidate_registry import attach_verified_learning
from project_orchestrator.capability_applicability import (
    build_applicability_index,
    match_applicable_capabilities,
)
from project_orchestrator.learning import LearningMemoryError, validate_lesson


def _memory_and_catalog():
    lesson = validate_lesson(
        {
            "project": "knowledge-engine",
            "repository": "jweter/knowledge-engine-ai",
            "source_type": "success_pattern",
            "source_ref": "knowledge-engine-ai#162",
            "symptom": "research report evidence boundary gap",
            "root_cause": "parser omitted direct-evidence invariant",
            "fix": "add deterministic parser invariant",
            "verification": "exact-head verification passed",
            "regression_protection": "report parser keeps the evidence boundary explicit",
            "residual_risk": "final readability remains Product Reality",
            "evidence_state": "VERIFIED",
        }
    )
    memory = {"schema_version": 1, "entries": [lesson]}
    catalog = {
        "schema_version": 1,
        "skills": [
            {
                "skill_key": "research-report-evidence-boundary-validator",
                "project": "knowledge-engine",
                "repository": "jweter/knowledge-engine-ai",
                "status": "PILOT_READY",
                "authority": "ADVISORY_ONLY",
                "source_lesson_ids": [lesson["lesson_id"]],
                "history_refs": ["commit:" + ("a" * 40)],
                "path_patterns": [
                    "knowledge_engine_ai/copilot/research_report.py",
                    "tests/copilot/test_research_report.py",
                ],
                "task_triggers": ["research report", "missing direct evidence"],
                "problem": "report output can cross an evidence boundary",
                "prevention_rule": "pair model guidance with deterministic validation",
                "verification_commands": ["python engineering/preflight.py"],
                "residual_risk": "human readability remains separate",
                "effectiveness_state": "UNMEASURED",
                "product_reality_may_be_inferred": False,
            }
        ],
    }
    return memory, catalog


def _candidate(**overrides):
    item = {
        "project": "knowledge-engine",
        "repository": "jweter/knowledge-engine-ai",
        "source_type": "decomposition",
        "source_ref": "knowledge-engine-ai#200/report",
        "source_revision": "rev-1",
        "source_state": "OPEN",
        "priority": "P5",
        "paths": ["knowledge_engine_ai/copilot/research_report.py"],
        "dependencies": [],
        "task": "strengthen research report provenance",
        "conflict_free": True,
    }
    item.update(overrides)
    return item


def test_known_verified_capability_is_attached_by_explicit_path():
    memory, catalog = _memory_and_catalog()
    attached = attach_verified_learning(_candidate(), memory, catalog)
    assert attached["learning_gate"] == "VERIFIED_CAPABILITIES_SELECTED"
    assert len(attached["capabilities_to_reuse"]) == 1
    ref = attached["capability_reuse_refs"][0]
    assert ref["evidence_state"] == "VERIFIED"
    assert ref["matched_paths"] == [
        "knowledge_engine_ai/copilot/research_report.py"
    ]
    assert ref["reuse_outcome"] == "PENDING_VERIFIED_OUTCOME"
    assert ref["product_reality_may_be_inferred"] is False
    assert "prevention_rule" not in ref


def test_unrelated_same_repository_work_does_not_receive_capability():
    memory, catalog = _memory_and_catalog()
    attached = attach_verified_learning(
        _candidate(paths=["knowledge_engine_ai/providers/search.py"], task="provider retry"),
        memory,
        catalog,
    )
    assert attached["learning_gate"] == "NO_EXPLICIT_CAPABILITY_MATCH"
    assert attached["capabilities_to_reuse"] == []


def test_declared_task_trigger_can_match_without_path_overlap():
    memory, catalog = _memory_and_catalog()
    index = build_applicability_index(catalog, memory)
    result = match_applicable_capabilities(
        index,
        project="knowledge-engine",
        repository="jweter/knowledge-engine-ai",
        changed_paths=["knowledge_engine_ai/other.py"],
        task_summary="handle missing direct evidence in the research report",
    )
    assert len(result["applicable_capabilities"]) == 1
    assert result["applicable_capabilities"][0]["matched_task_triggers"]


def test_repository_boundary_prevents_cross_repo_reuse():
    memory, catalog = _memory_and_catalog()
    index = build_applicability_index(catalog, memory)
    result = match_applicable_capabilities(
        index,
        project="knowledge-engine",
        repository="jweter/knowledge-engine-core",
        changed_paths=["knowledge_engine_ai/copilot/research_report.py"],
        task_summary="research report",
    )
    assert result["selection_state"] == "NO_EXPLICIT_CAPABILITY_MATCH"


def test_unverified_learning_cannot_back_capability_index():
    memory, catalog = _memory_and_catalog()
    memory["entries"][0]["evidence_state"] = "PENDING"
    with pytest.raises(LearningMemoryError, match="only VERIFIED"):
        build_applicability_index(catalog, memory)
