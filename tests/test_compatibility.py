import pytest

from project_orchestrator.compatibility import (
    CompatibilityError,
    plan_change,
    validate_boundary,
    validate_contract,
)


def contract() -> dict:
    return {
        "schema_version": 1,
        "project": "knowledge-engine",
        "roles": {
            "core": "jweter/knowledge-engine-core",
            "ai": "jweter/knowledge-engine-ai",
            "web": "jweter/knowledge-engine-web",
        },
        "edges": [
            {
                "producer": "core",
                "consumer": "ai",
                "contract_kinds": ["cli_json", "provenance_contract"],
                "boundary": "knowledge_engine_ai.ke_client",
                "rule": "AI wraps Core CLI.",
            },
            {
                "producer": "core",
                "consumer": "web",
                "contract_kinds": ["database_schema", "provenance_contract"],
                "boundary": "read_only_sqlalchemy_reflection",
                "rule": "Web reflects Core database.",
            },
            {
                "producer": "ai",
                "consumer": "web",
                "contract_kinds": ["typed_model", "research_report_contract"],
                "boundary": "knowledge_engine_ai_python_package",
                "rule": "Web imports AI.",
            },
        ],
        "forbidden_boundaries": [
            {
                "source": "web",
                "target": "core",
                "kind": "raw_subprocess",
                "reason": "Use AI ke_client.",
            }
        ],
        "change_kinds_requiring_review": [
            "cli_json",
            "database_schema",
            "typed_model",
            "provenance_contract",
            "research_report_contract",
            "package_config",
        ],
        "breaking_change_rule": "prepare dependents first",
        "additive_change_rule": "verify consumers",
    }


def test_contract_validates() -> None:
    validate_contract(contract())


def test_core_cli_change_identifies_ai_and_transitive_web() -> None:
    plan = plan_change(
        {"producer_role": "core", "contract_kinds": ["cli_json"], "breaking": False},
        contract(),
    )
    assert plan["compatibility_state"] == "REVIEW_REQUIRED"
    assert plan["direct_consumers"] == ["ai"]
    assert plan["transitive_consumers"] == ["web"]
    assert [action["repository_role"] for action in plan["actions"][:2]] == [
        "ai",
        "web",
    ]


def test_core_database_change_identifies_web_directly() -> None:
    plan = plan_change(
        {
            "producer_role": "core",
            "contract_kinds": ["database_schema"],
            "breaking": False,
        },
        contract(),
    )
    assert plan["direct_consumers"] == ["web"]
    assert plan["transitive_consumers"] == []


def test_breaking_ai_contract_prepares_web_before_producer_merge() -> None:
    plan = plan_change(
        {
            "producer_role": "ai",
            "contract_kinds": ["typed_model"],
            "breaking": True,
        },
        contract(),
    )
    assert plan["compatibility_state"] == "COORDINATED_BREAKING_CHANGE"
    actions = [item["action"] for item in plan["actions"]]
    assert "prepare_compatibility" in actions
    assert actions.index("prepare_compatibility") < actions.index(
        "merge_producer_dependency_safe"
    )


def test_unknown_contract_kind_fails_closed() -> None:
    with pytest.raises(CompatibilityError, match="unknown contract_kinds"):
        plan_change(
            {"producer_role": "core", "contract_kinds": ["mystery"], "breaking": False},
            contract(),
        )


def test_web_raw_core_subprocess_is_forbidden() -> None:
    result = validate_boundary("web", "core", "raw_subprocess", contract())
    assert result["allowed"] is False


def test_unlisted_boundary_does_not_override_repository_authority() -> None:
    result = validate_boundary("ai", "core", "documented_cli_call", contract())
    assert result["allowed"] is True
    assert "governing documents remain authoritative" in result["reason"]
