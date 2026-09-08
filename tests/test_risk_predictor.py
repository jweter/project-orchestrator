from project_orchestrator.risk_predictor import build_pre_change_risk_profile


def memory():
    return {
        "schema_version": 1,
        "entries": [
            {
                "project": "everward",
                "repository": "jweter/Project-Everward",
                "source_type": "ci_failure",
                "source_ref": "Project-Everward#201",
                "symptom": "save migration ctest failed in src/simulation/tests/save_data_tests.cpp",
                "root_cause": "append-only JSON set created a duplicate save_version key",
                "fix": "replace the existing save_version field explicitly",
                "verification": "ctest and full preflight passed",
                "regression_protection": "use replace semantics for existing migration schema keys and run save migration fixtures",
                "residual_risk": "future migration transforms may append duplicate existing keys",
                "evidence_state": "VERIFIED",
            },
            {
                "project": "rocksmith",
                "repository": "jweter/rocksmith-cdlc-generator",
                "source_type": "ci_failure",
                "source_ref": "rocksmith#500",
                "symptom": "EOF export timing test failed",
                "root_cause": "boundary conversion used inconsistent rounding",
                "fix": "centralize EOF boundary conversion",
                "verification": "timing regression tests passed",
                "regression_protection": "run canonical EOF timing fixtures before export changes",
                "residual_risk": "human musical acceptance remains separate",
                "evidence_state": "VERIFIED",
            },
            {
                "project": "everward",
                "repository": "jweter/Project-Everward",
                "source_type": "success_pattern",
                "source_ref": "Project-Everward#202",
                "symptom": "persistence change reached GREEN in one governed repair cycle",
                "root_cause": "prior migration prevention was retrieved before editing",
                "fix": "reuse deterministic save fixtures before changing persistence",
                "verification": "targeted tests, preflight, and fresh CI passed",
                "regression_protection": "retrieve persistence lessons and run deterministic save fixtures before mutation",
                "residual_risk": "future schema authority can supersede this pattern",
                "evidence_state": "VERIFIED",
            },
        ],
    }


def test_predictor_surfaces_matching_failure_class_and_prevention():
    result = build_pre_change_risk_profile(
        memory(),
        repository="jweter/Project-Everward",
        changed_paths=["src/simulation/include/everward/simulation/save_data.hpp"],
        change_summary="modify deterministic save migration JSON schema",
    )
    assert result["historical_signal_state"] == "HISTORICAL_SIGNALS_PRESENT"
    assert result["historical_failure_classes"][0]["failure_class"] == "TEST_FAILURE"
    assert any("replace semantics" in rule for rule in result["prevention_checklist"])
    assert result["risk_probability"] == "UNKNOWN"
    assert result["risk_severity"] == "UNKNOWN"


def test_predictor_separates_success_patterns_from_failures():
    result = build_pre_change_risk_profile(
        memory(),
        repository="jweter/Project-Everward",
        changed_paths=["src/simulation/save/persistence.cpp"],
        change_summary="persistence save fixtures migration",
    )
    assert len(result["successful_patterns"]) == 1
    assert "deterministic save fixtures" in result["successful_patterns"][0]["successful_strategy"]


def test_predictor_does_not_cross_pollinate_products():
    result = build_pre_change_risk_profile(
        memory(),
        repository="jweter/Project-Everward",
        changed_paths=["src/simulation/include/everward/simulation/save_data.hpp"],
        change_summary="save migration",
    )
    refs = {
        ref
        for group in result["historical_failure_classes"]
        for ref in group["evidence_refs"]
    }
    assert "rocksmith#500" not in refs


def test_no_history_remains_explicitly_unknown_not_low_risk():
    result = build_pre_change_risk_profile(
        memory(),
        repository="jweter/Project-Everward",
        changed_paths=["src/simulation/novel_system.cpp"],
        change_summary="unrelated novel subsystem",
    )
    assert result["historical_signal_state"] == "NO_RELEVANT_VERIFIED_HISTORY"
    assert result["risk_probability"] == "UNKNOWN"
    assert result["prevention_checklist"] == []


def test_generic_same_repository_path_tokens_do_not_create_false_risk():
    result = build_pre_change_risk_profile(
        memory(),
        repository="jweter/Project-Everward",
        changed_paths=["src/simulation/new_unrelated_feature.cpp"],
        change_summary="unrelated subsystem",
    )
    assert result["historical_signal_state"] == "NO_RELEVANT_VERIFIED_HISTORY"
    assert result["historical_failure_classes"] == []
    assert result["successful_patterns"] == []
