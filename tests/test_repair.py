from __future__ import annotations

from pathlib import Path

from project_orchestrator.repair import (
    MAX_REPAIR_ITERATIONS,
    approved_repair_commands,
    build_repair_plan,
    execute_repair_plan,
    fingerprint_failure,
)


def test_fingerprint_prefers_import_sort_over_generic_failed() -> None:
    log = """
    I001 [*] Import block is un-sorted or un-formatted
    Found 2 errors.
    Quality preflight stopped: lint failed with exit code 1.
    """
    result = fingerprint_failure(log)
    assert result.failure_class == "IMPORT_SORT"
    assert result.confidence == "HIGH"


def test_format_uses_existing_preflight_format_apply_authority() -> None:
    cp = {"preflight": {"format_apply": ["poetry", "run", "ruff", "format", "."]}}
    plan = build_repair_plan(
        repository="jweter/knowledge-engine-core",
        log_text="1 file would be reformatted",
        control_plane=cp,
    )
    assert plan["eligible_for_auto_repair"] is True
    assert plan["commands"] == [["poetry", "run", "ruff", "format", "."]]


def test_import_sort_requires_explicit_repository_opt_in() -> None:
    cp = {"preflight": {"format_apply": ["ruff", "format", "."]}}
    plan = build_repair_plan(
        repository="jweter/example",
        log_text="I001 Import block is un-sorted",
        control_plane=cp,
    )
    assert plan["eligible_for_auto_repair"] is False
    assert plan["commands"] == []


def test_explicit_import_sort_policy_is_executable() -> None:
    cp = {
        "deterministic_repair": {
            "approved_classes": ["IMPORT_SORT"],
            "commands": {"IMPORT_SORT": [["poetry", "run", "ruff", "check", "--fix", "."]]},
        }
    }
    assert approved_repair_commands(cp, "IMPORT_SORT") == [
        ["poetry", "run", "ruff", "check", "--fix", "."]
    ]


def test_behavioral_test_failure_never_auto_repairs_even_if_command_is_declared() -> None:
    cp = {
        "deterministic_repair": {
            "approved_classes": ["TEST_FAILURE"],
            "commands": {"TEST_FAILURE": [["python", "-m", "pytest", "--update-snapshots"]]},
        }
    }
    plan = build_repair_plan(
        repository="jweter/example",
        log_text="pytest FAILED tests/test_x.py::test_value",
        control_plane=cp,
    )
    assert plan["failure_class"] == "TEST_FAILURE"
    assert plan["eligible_for_auto_repair"] is False


def test_repair_budget_fails_closed() -> None:
    cp = {"preflight": {"format_apply": ["ruff", "format", "."]}}
    plan = build_repair_plan(
        repository="jweter/example",
        log_text="would be reformatted",
        control_plane=cp,
        iteration=MAX_REPAIR_ITERATIONS + 1,
    )
    assert plan["eligible_for_auto_repair"] is False
    assert "budget exhausted" in plan["reason"]


def test_execute_non_executable_plan_is_side_effect_free(tmp_path: Path) -> None:
    plan = {
        "eligible_for_auto_repair": False,
        "commands": [["python", "-c", "raise SystemExit(1)"]],
    }
    result = execute_repair_plan(plan, repo_root=tmp_path)
    assert result["execution_status"] == "NOT_EXECUTED"
    assert result["executions"] == []
