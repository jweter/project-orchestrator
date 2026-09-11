from __future__ import annotations

from project_orchestrator.fast_gate import build_repository_fast_gate_plan


def test_existing_fast_preflight_checks_are_valid_authority() -> None:
    control_plane = {
        "preflight": {
            "checks": [
                {"id": "control", "argv": ["python", "engineering/validate.py"], "fast": True},
                {"id": "compile", "argv": ["python", "-m", "compileall", "-q", "src"], "fast": True},
                {"id": "tests", "argv": ["python", "-m", "pytest", "-q"], "fast": False},
            ]
        }
    }
    plan = build_repository_fast_gate_plan(
        repository="owner/repo",
        control_plane=control_plane,
        changed_paths=["src/example.py"],
    )
    assert plan["status"] == "READY"
    assert plan["authority_source"] == "preflight.checks[].fast=true"
    assert [item["id"] for item in plan["commands"]] == ["control", "compile"]
    assert plan["canonical_preflight_still_required"] is True


def test_explicit_fast_gate_policy_takes_precedence() -> None:
    control_plane = {
        "fast_gate": {
            "enabled": True,
            "commands": [
                {"id": "ruff", "argv": ["ruff", "check", "src"], "extensions": [".py"]}
            ],
        },
        "preflight": {
            "checks": [
                {"id": "legacy", "argv": ["python", "legacy.py"], "fast": True}
            ]
        },
    }
    plan = build_repository_fast_gate_plan(
        repository="owner/repo",
        control_plane=control_plane,
        changed_paths=["src/example.py"],
    )
    assert [item["id"] for item in plan["commands"]] == ["ruff"]
    assert "authority_source" not in plan


def test_no_authority_fails_closed() -> None:
    plan = build_repository_fast_gate_plan(
        repository="owner/repo",
        control_plane={"preflight": {"checks": [{"id": "tests", "argv": ["pytest"], "fast": False}]}},
        changed_paths=["src/example.py"],
    )
    assert plan["status"] == "NOT_AUTHORIZED"
    assert plan["commands"] == []
