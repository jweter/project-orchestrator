import pytest

from project_orchestrator.area_learning import retrieve_learning


def memory():
    return {
        "schema_version": 1,
        "entries": [
            {
                "project": "everward",
                "repository": "jweter/Project-Everward",
                "source_type": "ci_failure",
                "source_ref": "Project-Everward#200",
                "symptom": "save migration test failed in src/save/migration.cpp",
                "root_cause": "new state field was not given a deterministic migration default",
                "fix": "add explicit migration default and version fixture",
                "verification": "migration suite and full CI passed",
                "regression_protection": "before editing save migration state, add a prior-version fixture with deterministic defaults",
                "residual_risk": "future schema additions still require explicit migration review",
                "evidence_state": "VERIFIED",
            },
            {
                "project": "rocksmith",
                "repository": "jweter/rocksmith-cdlc-generator",
                "source_type": "ci_failure",
                "source_ref": "rocksmith#500",
                "symptom": "EOF export timing boundary rounded sustain incorrectly",
                "root_cause": "boundary conversion used inconsistent tick rounding",
                "fix": "centralize EOF boundary conversion",
                "verification": "EOF regression suite passed",
                "regression_protection": "reuse canonical EOF boundary conversion and run timing regression fixtures",
                "residual_risk": "human musical acceptance remains separate",
                "evidence_state": "VERIFIED",
            },
            {
                "project": "portfolio",
                "repository": "jweter/project-orchestrator",
                "source_type": "governance",
                "source_ref": "project-orchestrator#29",
                "symptom": "ordinary governed changes were written directly to the default branch",
                "root_cause": "mutation occurred before branch promotion path establishment",
                "fix": "create branch before first governed mutation",
                "verification": "subsequent work used branch-first promotion",
                "regression_protection": "before repository mutation establish feature branch and promotion path",
                "residual_risk": "connector permission does not imply repository authority",
                "evidence_state": "VERIFIED",
            },
        ],
    }


def test_retrieves_same_repository_area_lesson():
    result = retrieve_learning(
        memory(),
        repository="jweter/Project-Everward",
        changed_paths=["src/save/migration.cpp"],
        change_summary="extend deterministic save migration",
    )
    assert result["learning_gate"] == "APPLY_RELEVANT_PREVENTION"
    assert result["relevant_lessons"][0]["repository"] == "jweter/Project-Everward"
    assert "deterministic defaults" in result["relevant_lessons"][0]["prevention_rule"]


def test_does_not_cross_pollinate_unrelated_product_lessons():
    result = retrieve_learning(
        memory(),
        repository="jweter/Project-Everward",
        changed_paths=["src/save/migration.cpp"],
    )
    assert all(item["repository"] != "jweter/rocksmith-cdlc-generator" for item in result["relevant_lessons"])


def test_portfolio_lesson_requires_actual_query_overlap():
    result = retrieve_learning(
        memory(),
        repository="jweter/Project-Everward",
        changed_paths=["src/simulation/mining.cpp"],
        change_summary="change asteroid mining yield",
    )
    assert all(item["repository"] != "jweter/project-orchestrator" for item in result["relevant_lessons"])


def test_portfolio_governance_lesson_surfaces_for_branch_mutation_change():
    result = retrieve_learning(
        memory(),
        repository="jweter/Project-Everward",
        changed_paths=["engineering/control-plane.json"],
        change_summary="repository mutation branch promotion path",
    )
    assert any(item["repository"] == "jweter/project-orchestrator" for item in result["relevant_lessons"])


def test_rejects_empty_change_scope():
    with pytest.raises(ValueError, match="changed_paths"):
        retrieve_learning(memory(), repository="jweter/Project-Everward", changed_paths=[])
