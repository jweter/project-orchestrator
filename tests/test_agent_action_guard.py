from __future__ import annotations

import pytest

from project_orchestrator.agent_action_guard import (
    AgentActionGuardError,
    build_agent_action_context,
    evaluate_agent_action_guard,
)


SHA = "a" * 40


BASE_POLICY = {
    "max_compute_seconds_without_approval": 3600,
    "hard_compute_seconds": 14400,
    "max_network_calls_without_approval": 50,
    "hard_network_calls": 500,
    "max_write_operations_without_approval": 10,
    "hard_write_operations": 100,
    "max_concurrency_without_approval": 4,
    "hard_concurrency": 12,
    "max_external_cost_usd_without_approval": 0,
    "hard_external_cost_usd": 50,
}


def context(**overrides):
    values = {
        "project": "knowledge-engine",
        "repository": "jweter/knowledge-engine-core",
        "head_sha": SHA,
        "action_id": "repair-core-preflight",
        "objective": "repair the verified canonical preflight failure",
        "authority_ref": "issue:#57",
        "scope_refs": ["tests/test_x.py"],
        "context_refs": ["engineering/control-plane.json", "docs/ARCHITECTURE.md"],
        "evidence_refs": ["worker:#56:canonical-preflight"],
        "prior_lesson_refs": [],
        "context_lookup_performed": True,
        "learning_lookup_performed": True,
        "failure_or_retry": False,
        "exact_error_ref": None,
        "attempt": 1,
        "reserved_human_decision": False,
        "destructive": False,
        "irreversible": False,
    }
    values.update(overrides)
    return build_agent_action_context(**values)


def resources(**overrides):
    values = {
        "compute_seconds_estimate": 900,
        "network_calls_estimate": 10,
        "write_operations_estimate": 2,
        "concurrency_estimate": 1,
        "uses_paid_external_service": False,
        "external_cost_usd_estimate": None,
    }
    values.update(overrides)
    return values


def test_ready_when_context_complete_and_budget_bounded() -> None:
    result = evaluate_agent_action_guard(
        context=context(),
        resources=resources(),
        policy=BASE_POLICY,
    )
    assert result["state"] == "READY"
    assert result["commands"] == []
    assert result["execution_authority"] == "GATE_ONLY"
    assert result["existing_gates_preserved"] is True


def test_retry_requires_exact_error_evidence() -> None:
    result = evaluate_agent_action_guard(
        context=context(failure_or_retry=True, attempt=2, exact_error_ref=None),
        resources=resources(),
        policy=BASE_POLICY,
    )
    assert result["state"] == "GATHER_CONTEXT"
    assert "exact_error_ref_for_failure_or_retry" in result["missing_context"]


def test_retry_with_exact_error_can_proceed() -> None:
    result = evaluate_agent_action_guard(
        context=context(
            failure_or_retry=True,
            attempt=2,
            exact_error_ref="logs:preflight-core:test_discovery_broker",
        ),
        resources=resources(),
        policy=BASE_POLICY,
    )
    assert result["state"] == "READY"


def test_context_and_learning_lookup_are_first_class_gates() -> None:
    result = evaluate_agent_action_guard(
        context=context(context_lookup_performed=False, learning_lookup_performed=False),
        resources=resources(),
        policy=BASE_POLICY,
    )
    assert result["state"] == "GATHER_CONTEXT"
    assert result["missing_context"] == [
        "context_lookup_not_performed",
        "learning_lookup_not_performed",
    ]


def test_paid_external_service_with_unknown_cost_requires_approval() -> None:
    result = evaluate_agent_action_guard(
        context=context(),
        resources=resources(uses_paid_external_service=True, external_cost_usd_estimate=None),
        policy=BASE_POLICY,
    )
    assert result["state"] == "APPROVAL_REQUIRED"
    assert "paid_external_cost_unknown" in result["approval_reasons"]


def test_soft_resource_budget_requires_approval() -> None:
    result = evaluate_agent_action_guard(
        context=context(),
        resources=resources(compute_seconds_estimate=7200, concurrency_estimate=6),
        policy=BASE_POLICY,
    )
    assert result["state"] == "APPROVAL_REQUIRED"
    assert "compute_seconds_estimate_exceeds_autonomous_budget" in result["approval_reasons"]
    assert "concurrency_estimate_exceeds_autonomous_budget" in result["approval_reasons"]


def test_hard_cap_is_forbidden_even_if_context_is_missing() -> None:
    result = evaluate_agent_action_guard(
        context=context(context_lookup_performed=False),
        resources=resources(external_cost_usd_estimate=75, uses_paid_external_service=True),
        policy=BASE_POLICY,
    )
    assert result["state"] == "FORBIDDEN"
    assert result["forbidden_reasons"] == ["external_cost_estimate_exceeds_hard_cap"]


def test_reserved_destructive_or_irreversible_action_requires_approval() -> None:
    result = evaluate_agent_action_guard(
        context=context(reserved_human_decision=True, destructive=True, irreversible=True),
        resources=resources(),
        policy=BASE_POLICY,
    )
    assert result["state"] == "APPROVAL_REQUIRED"
    assert set(result["approval_reasons"]) == {
        "destructive_action",
        "irreversible_action",
        "reserved_human_decision",
    }


def test_invalid_policy_threshold_order_fails_closed() -> None:
    policy = {**BASE_POLICY, "hard_concurrency": 2}
    with pytest.raises(AgentActionGuardError, match="hard_concurrency"):
        evaluate_agent_action_guard(
            context=context(),
            resources=resources(),
            policy=policy,
        )
