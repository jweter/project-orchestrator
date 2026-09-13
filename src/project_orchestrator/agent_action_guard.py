from __future__ import annotations

from typing import Any
import re


ACTION_STATES = {"READY", "GATHER_CONTEXT", "APPROVAL_REQUIRED", "FORBIDDEN"}
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class AgentActionGuardError(ValueError):
    """Raised when an action-context or resource envelope is malformed."""


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgentActionGuardError(f"{name} must be a non-empty string")
    return value.strip()


def _sha(value: Any) -> str:
    text = _nonempty(value, "head_sha")
    if not _SHA_RE.fullmatch(text):
        raise AgentActionGuardError("head_sha must be a 40-character lowercase hex SHA")
    return text


def _bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise AgentActionGuardError(f"{name} must be boolean")
    return value


def _string_list(value: Any, name: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise AgentActionGuardError(f"{name} must be a list of non-empty strings")
    normalized = sorted({item.strip() for item in value})
    if not allow_empty and not normalized:
        raise AgentActionGuardError(f"{name} must not be empty")
    return normalized


def _nonnegative_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise AgentActionGuardError(f"{name} must be a non-negative number")
    return float(value)


def _nonnegative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AgentActionGuardError(f"{name} must be a non-negative integer")
    return value


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise AgentActionGuardError(f"{name} must be a positive integer")
    return value


def build_agent_action_context(
    *,
    project: str,
    repository: str,
    head_sha: str,
    action_id: str,
    objective: str,
    authority_ref: str,
    scope_refs: list[str],
    context_refs: list[str],
    evidence_refs: list[str],
    prior_lesson_refs: list[str] | None = None,
    context_lookup_performed: bool,
    learning_lookup_performed: bool,
    failure_or_retry: bool = False,
    exact_error_ref: str | None = None,
    attempt: int = 1,
    reserved_human_decision: bool = False,
    destructive: bool = False,
    irreversible: bool = False,
) -> dict[str, Any]:
    """Build a deterministic context package before autonomous action.

    This package is descriptive only. It grants no execution authority.
    """

    exact_error = None
    if exact_error_ref is not None:
        exact_error = _nonempty(exact_error_ref, "exact_error_ref")

    return {
        "schema_version": 1,
        "project": _nonempty(project, "project"),
        "repository": _nonempty(repository, "repository"),
        "head_sha": _sha(head_sha),
        "action_id": _nonempty(action_id, "action_id"),
        "objective": _nonempty(objective, "objective"),
        "authority_ref": _nonempty(authority_ref, "authority_ref"),
        "scope_refs": _string_list(scope_refs, "scope_refs"),
        "context_refs": _string_list(context_refs, "context_refs"),
        "evidence_refs": _string_list(evidence_refs, "evidence_refs"),
        "prior_lesson_refs": _string_list(prior_lesson_refs or [], "prior_lesson_refs"),
        "context_lookup_performed": _bool(context_lookup_performed, "context_lookup_performed"),
        "learning_lookup_performed": _bool(learning_lookup_performed, "learning_lookup_performed"),
        "failure_or_retry": _bool(failure_or_retry, "failure_or_retry"),
        "exact_error_ref": exact_error,
        "attempt": _positive_int(attempt, "attempt"),
        "reserved_human_decision": _bool(reserved_human_decision, "reserved_human_decision"),
        "destructive": _bool(destructive, "destructive"),
        "irreversible": _bool(irreversible, "irreversible"),
    }


def _normalize_context(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AgentActionGuardError("context must be an object")
    return build_agent_action_context(
        project=raw.get("project"),
        repository=raw.get("repository"),
        head_sha=raw.get("head_sha"),
        action_id=raw.get("action_id"),
        objective=raw.get("objective"),
        authority_ref=raw.get("authority_ref"),
        scope_refs=raw.get("scope_refs", []),
        context_refs=raw.get("context_refs", []),
        evidence_refs=raw.get("evidence_refs", []),
        prior_lesson_refs=raw.get("prior_lesson_refs", []),
        context_lookup_performed=raw.get("context_lookup_performed"),
        learning_lookup_performed=raw.get("learning_lookup_performed"),
        failure_or_retry=raw.get("failure_or_retry", False),
        exact_error_ref=raw.get("exact_error_ref"),
        attempt=raw.get("attempt", 1),
        reserved_human_decision=raw.get("reserved_human_decision", False),
        destructive=raw.get("destructive", False),
        irreversible=raw.get("irreversible", False),
    )


def _normalize_resources(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AgentActionGuardError("resources must be an object")
    paid = _bool(raw.get("uses_paid_external_service", False), "uses_paid_external_service")
    external_cost = raw.get("external_cost_usd_estimate")
    if external_cost is not None:
        external_cost = _nonnegative_number(external_cost, "external_cost_usd_estimate")
    return {
        "compute_seconds_estimate": _nonnegative_number(
            raw.get("compute_seconds_estimate"), "compute_seconds_estimate"
        ),
        "network_calls_estimate": _nonnegative_int(
            raw.get("network_calls_estimate"), "network_calls_estimate"
        ),
        "write_operations_estimate": _nonnegative_int(
            raw.get("write_operations_estimate"), "write_operations_estimate"
        ),
        "concurrency_estimate": _positive_int(
            raw.get("concurrency_estimate"), "concurrency_estimate"
        ),
        "uses_paid_external_service": paid,
        "external_cost_usd_estimate": external_cost,
    }


def _normalize_policy(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AgentActionGuardError("policy must be an object")
    fields = {
        "max_compute_seconds_without_approval": _nonnegative_number,
        "hard_compute_seconds": _nonnegative_number,
        "max_network_calls_without_approval": _nonnegative_int,
        "hard_network_calls": _nonnegative_int,
        "max_write_operations_without_approval": _nonnegative_int,
        "hard_write_operations": _nonnegative_int,
        "max_concurrency_without_approval": _positive_int,
        "hard_concurrency": _positive_int,
        "max_external_cost_usd_without_approval": _nonnegative_number,
        "hard_external_cost_usd": _nonnegative_number,
    }
    result: dict[str, Any] = {}
    for name, normalizer in fields.items():
        if name not in raw:
            raise AgentActionGuardError(f"policy missing required field: {name}")
        result[name] = normalizer(raw[name], name)
    if result["hard_compute_seconds"] < result["max_compute_seconds_without_approval"]:
        raise AgentActionGuardError("hard_compute_seconds must be >= approval threshold")
    if result["hard_network_calls"] < result["max_network_calls_without_approval"]:
        raise AgentActionGuardError("hard_network_calls must be >= approval threshold")
    if result["hard_write_operations"] < result["max_write_operations_without_approval"]:
        raise AgentActionGuardError("hard_write_operations must be >= approval threshold")
    if result["hard_concurrency"] < result["max_concurrency_without_approval"]:
        raise AgentActionGuardError("hard_concurrency must be >= approval threshold")
    if result["hard_external_cost_usd"] < result["max_external_cost_usd_without_approval"]:
        raise AgentActionGuardError("hard_external_cost_usd must be >= approval threshold")
    return result


def evaluate_agent_action_guard(
    *,
    context: dict[str, Any],
    resources: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    """Decide whether an autonomous action has enough context and budget to proceed.

    The guard is planning authority only. It never executes commands or weakens
    repository-native CI, review, policy, security, provenance, or Product Reality gates.
    """

    ctx = _normalize_context(context)
    res = _normalize_resources(resources)
    pol = _normalize_policy(policy)

    missing_context: list[str] = []
    if not ctx["context_lookup_performed"]:
        missing_context.append("context_lookup_not_performed")
    if not ctx["learning_lookup_performed"]:
        missing_context.append("learning_lookup_not_performed")
    if not ctx["scope_refs"]:
        missing_context.append("scope_refs")
    if not ctx["context_refs"]:
        missing_context.append("context_refs")
    if not ctx["evidence_refs"]:
        missing_context.append("evidence_refs")
    if (ctx["failure_or_retry"] or ctx["attempt"] > 1) and not ctx["exact_error_ref"]:
        missing_context.append("exact_error_ref_for_failure_or_retry")

    forbidden: list[str] = []
    approval: list[str] = []

    hard_checks = [
        ("compute_seconds", res["compute_seconds_estimate"], pol["hard_compute_seconds"]),
        ("network_calls", res["network_calls_estimate"], pol["hard_network_calls"]),
        ("write_operations", res["write_operations_estimate"], pol["hard_write_operations"]),
        ("concurrency", res["concurrency_estimate"], pol["hard_concurrency"]),
    ]
    for label, value, limit in hard_checks:
        if value > limit:
            forbidden.append(f"{label}_estimate_exceeds_hard_cap")

    cost = res["external_cost_usd_estimate"]
    if cost is not None and cost > pol["hard_external_cost_usd"]:
        forbidden.append("external_cost_estimate_exceeds_hard_cap")

    if ctx["reserved_human_decision"]:
        approval.append("reserved_human_decision")
    if ctx["destructive"]:
        approval.append("destructive_action")
    if ctx["irreversible"]:
        approval.append("irreversible_action")
    if res["uses_paid_external_service"] and cost is None:
        approval.append("paid_external_cost_unknown")

    soft_checks = [
        (
            "compute_seconds",
            res["compute_seconds_estimate"],
            pol["max_compute_seconds_without_approval"],
        ),
        (
            "network_calls",
            res["network_calls_estimate"],
            pol["max_network_calls_without_approval"],
        ),
        (
            "write_operations",
            res["write_operations_estimate"],
            pol["max_write_operations_without_approval"],
        ),
        (
            "concurrency",
            res["concurrency_estimate"],
            pol["max_concurrency_without_approval"],
        ),
    ]
    for label, value, limit in soft_checks:
        if value > limit:
            approval.append(f"{label}_estimate_exceeds_autonomous_budget")
    if cost is not None and cost > pol["max_external_cost_usd_without_approval"]:
        approval.append("external_cost_estimate_exceeds_autonomous_budget")

    if forbidden:
        state = "FORBIDDEN"
        reason = "resource estimate exceeds an explicit hard cap"
    elif missing_context:
        state = "GATHER_CONTEXT"
        reason = "required context/evidence is incomplete; do not act from inference"
    elif approval:
        state = "APPROVAL_REQUIRED"
        reason = "action reaches a resource, cost, destructive, irreversible, or reserved-human boundary"
    else:
        state = "READY"
        reason = "context is complete and the resource envelope is within autonomous policy"

    return {
        "schema_version": 1,
        "state": state,
        "reason": reason,
        "project": ctx["project"],
        "repository": ctx["repository"],
        "head_sha": ctx["head_sha"],
        "action_id": ctx["action_id"],
        "missing_context": sorted(missing_context),
        "approval_reasons": sorted(set(approval)),
        "forbidden_reasons": sorted(set(forbidden)),
        "resource_estimate": res,
        "execution_authority": "GATE_ONLY",
        "commands": [],
        "existing_gates_preserved": True,
    }
