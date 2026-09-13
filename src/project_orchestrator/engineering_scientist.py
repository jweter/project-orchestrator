from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict, deque
from typing import Any


class EngineeringScientistError(ValueError):
    """Raised when scientist-loop evidence violates the deterministic contract."""


_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ACTION_GUARD_STATES = {"READY", "GATHER_CONTEXT", "APPROVAL_REQUIRED", "FORBIDDEN"}
EXPERIMENT_STATES = {"PLANNED", "RUNNING", "COMPLETE", "ABORTED", "REJECTED"}
VERIFICATION_STATES = {"UNVERIFIED", "VERIFIED_PASS", "VERIFIED_FAIL"}
SCIENTIST_STAGES = (
    "OBSERVE",
    "FORMULATE_HYPOTHESIS",
    "RETRIEVE_PRIOR_EVIDENCE",
    "DESIGN_BOUNDED_EXPERIMENT",
    "ESTIMATE_COST",
    "ACTION_GUARD",
    "EXECUTE",
    "MEASURE",
    "COMPARE",
    "INDEPENDENT_VERIFY",
    "RECORD",
    "GENERALIZE",
    "REPEAT",
)
POSITIVE_PATTERN_OUTCOMES = {
    "PREVENTED_FAILURE",
    "EARLIER_DETECTION",
    "SHORTENED_REPAIR",
    "SUCCESSFUL_PATTERN_REUSE",
    "VERIFIED_CORRECTNESS_IMPROVEMENT",
}


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EngineeringScientistError(f"{name} must be a non-empty string")
    return value.strip()


def _sha(value: Any, name: str) -> str:
    text = _nonempty(value, name)
    if not _SHA_RE.fullmatch(text):
        raise EngineeringScientistError(f"{name} must be a 40-character lowercase hex SHA")
    return text


def _bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise EngineeringScientistError(f"{name} must be boolean")
    return value


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise EngineeringScientistError(f"{name} must be a positive integer")
    return value


def _nonnegative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EngineeringScientistError(f"{name} must be a non-negative integer")
    return value


def _nonnegative_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise EngineeringScientistError(f"{name} must be a non-negative number")
    return float(value)


def _string_list(value: Any, name: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise EngineeringScientistError(f"{name} must be a list of non-empty strings")
    normalized = sorted({item.strip() for item in value})
    if not allow_empty and not normalized:
        raise EngineeringScientistError(f"{name} must not be empty")
    return normalized


def _content_id(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def _normalize_measurement_contract(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise EngineeringScientistError("measurement_contract must be an object")
    contract_id = _nonempty(raw.get("contract_id"), "measurement_contract.contract_id")
    primary_metric = _nonempty(raw.get("primary_metric"), "measurement_contract.primary_metric")
    direction = raw.get("direction")
    if direction not in {"MINIMIZE", "MAXIMIZE"}:
        raise EngineeringScientistError("measurement_contract.direction must be MINIMIZE or MAXIMIZE")
    tolerance = _nonnegative_number(raw.get("tolerance", 0), "measurement_contract.tolerance")
    required_metrics = _string_list(
        raw.get("required_metrics", [primary_metric]),
        "measurement_contract.required_metrics",
        allow_empty=False,
    )
    if primary_metric not in required_metrics:
        raise EngineeringScientistError("primary_metric must be included in required_metrics")
    return {
        "contract_id": contract_id,
        "primary_metric": primary_metric,
        "direction": direction,
        "tolerance": tolerance,
        "required_metrics": required_metrics,
    }


def _normalize_resource_estimate(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise EngineeringScientistError("resource_estimate must be an object")
    cost = raw.get("external_cost_usd_estimate")
    if cost is not None:
        cost = _nonnegative_number(cost, "resource_estimate.external_cost_usd_estimate")
    return {
        "compute_seconds_estimate": _nonnegative_number(
            raw.get("compute_seconds_estimate", 0), "resource_estimate.compute_seconds_estimate"
        ),
        "network_calls_estimate": _nonnegative_int(
            raw.get("network_calls_estimate", 0), "resource_estimate.network_calls_estimate"
        ),
        "write_operations_estimate": _nonnegative_int(
            raw.get("write_operations_estimate", 0), "resource_estimate.write_operations_estimate"
        ),
        "concurrency_estimate": _positive_int(
            raw.get("concurrency_estimate", 1), "resource_estimate.concurrency_estimate"
        ),
        "external_cost_usd_estimate": cost,
        "exclusive_resources": _string_list(
            raw.get("exclusive_resources", []), "resource_estimate.exclusive_resources"
        ),
    }


def _normalize_metrics(raw: Any, required: list[str]) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise EngineeringScientistError("result_metrics must be an object")
    result: dict[str, float] = {}
    for key, value in raw.items():
        name = _nonempty(key, "result metric name")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise EngineeringScientistError(f"result metric {name} must be numeric")
        result[name] = float(value)
    missing = sorted(set(required) - set(result))
    if missing:
        raise EngineeringScientistError(f"result_metrics missing required metrics: {missing}")
    return result


def build_experiment_record(
    *,
    project: str,
    repository: str,
    baseline_head_sha: str,
    candidate_head_sha: str,
    hypothesis_id: str,
    hypothesis: str,
    baseline_ref: str,
    measurement_contract: dict[str, Any],
    scope_refs: list[str],
    evidence_refs: list[str],
    resource_estimate: dict[str, Any],
    action_guard_state: str,
    attempt: int = 1,
    parent_experiment_id: str | None = None,
    parent_hypothesis_id: str | None = None,
    state: str = "PLANNED",
    verification_state: str = "UNVERIFIED",
    result_metrics: dict[str, Any] | None = None,
    verification_evidence_refs: list[str] | None = None,
    safety_violations: list[str] | None = None,
) -> dict[str, Any]:
    """Build one evidence-bound experiment record with durable ancestry.

    The record never executes the experiment. Candidate implementations remain
    subject to the Agent Action Guard and all repository-native gates.
    """

    contract = _normalize_measurement_contract(measurement_contract)
    if action_guard_state not in ACTION_GUARD_STATES:
        raise EngineeringScientistError("action_guard_state is invalid")
    if state not in EXPERIMENT_STATES:
        raise EngineeringScientistError("state is invalid")
    if verification_state not in VERIFICATION_STATES:
        raise EngineeringScientistError("verification_state is invalid")
    attempt = _positive_int(attempt, "attempt")
    if attempt > 1 and parent_experiment_id is None:
        raise EngineeringScientistError("attempt > 1 requires parent_experiment_id")
    parent_experiment = None if parent_experiment_id is None else _nonempty(parent_experiment_id, "parent_experiment_id")
    parent_hypothesis = None if parent_hypothesis_id is None else _nonempty(parent_hypothesis_id, "parent_hypothesis_id")
    metrics = None
    if result_metrics is not None:
        metrics = _normalize_metrics(result_metrics, contract["required_metrics"])
    verification_refs = _string_list(
        verification_evidence_refs or [], "verification_evidence_refs"
    )
    violations = _string_list(safety_violations or [], "safety_violations")
    if state == "COMPLETE" and metrics is None:
        raise EngineeringScientistError("COMPLETE experiment requires result_metrics")
    if verification_state != "UNVERIFIED" and not verification_refs:
        raise EngineeringScientistError("verified experiment requires verification_evidence_refs")
    if state in {"RUNNING", "COMPLETE"} and action_guard_state != "READY":
        raise EngineeringScientistError("RUNNING/COMPLETE experiment requires action_guard_state=READY")

    design = {
        "project": _nonempty(project, "project"),
        "repository": _nonempty(repository, "repository"),
        "baseline_head_sha": _sha(baseline_head_sha, "baseline_head_sha"),
        "candidate_head_sha": _sha(candidate_head_sha, "candidate_head_sha"),
        "hypothesis_id": _nonempty(hypothesis_id, "hypothesis_id"),
        "hypothesis": _nonempty(hypothesis, "hypothesis"),
        "baseline_ref": _nonempty(baseline_ref, "baseline_ref"),
        "measurement_contract": contract,
        "scope_refs": _string_list(scope_refs, "scope_refs", allow_empty=False),
        "attempt": attempt,
        "parent_experiment_id": parent_experiment,
        "parent_hypothesis_id": parent_hypothesis,
    }
    experiment_id = _content_id(design)
    return {
        "schema_version": 1,
        "experiment_id": experiment_id,
        **design,
        "evidence_refs": _string_list(evidence_refs, "evidence_refs", allow_empty=False),
        "resource_estimate": _normalize_resource_estimate(resource_estimate),
        "action_guard_state": action_guard_state,
        "state": state,
        "verification_state": verification_state,
        "result_metrics": metrics,
        "verification_evidence_refs": verification_refs,
        "safety_violations": violations,
        "execution_authority": "RECORD_ONLY",
    }


def compare_hypotheses(experiments: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare independently verified hypotheses under one measurement contract.

    A winner is selected only when candidates share the same repository,
    baseline, and measurement contract and the leading primary metric exceeds
    the runner-up by more than the contract tolerance.
    """

    if not isinstance(experiments, list) or len(experiments) < 2:
        raise EngineeringScientistError("experiments must contain at least two records")
    normalized = [build_experiment_record(**_experiment_kwargs(item)) for item in experiments]
    repository_set = {item["repository"] for item in normalized}
    baseline_set = {(item["baseline_head_sha"], item["baseline_ref"]) for item in normalized}
    contracts = {
        json.dumps(item["measurement_contract"], sort_keys=True, separators=(",", ":"))
        for item in normalized
    }
    incompatibilities: list[str] = []
    if len(repository_set) != 1:
        incompatibilities.append("repository_mismatch")
    if len(baseline_set) != 1:
        incompatibilities.append("baseline_mismatch")
    if len(contracts) != 1:
        incompatibilities.append("measurement_contract_mismatch")
    if incompatibilities:
        return {
            "schema_version": 1,
            "state": "INCOMPARABLE",
            "winner_experiment_id": None,
            "reasons": incompatibilities,
            "eligible_experiment_ids": [],
            "rejected": [],
            "promotion_authority": "COMPARISON_ONLY",
        }

    contract = normalized[0]["measurement_contract"]
    primary = contract["primary_metric"]
    eligible: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for item in normalized:
        if item["state"] != "COMPLETE":
            rejected.append({"experiment_id": item["experiment_id"], "reason": "experiment_not_complete"})
            continue
        if item["verification_state"] != "VERIFIED_PASS":
            rejected.append({"experiment_id": item["experiment_id"], "reason": "independent_verification_not_passed"})
            continue
        if item["safety_violations"]:
            rejected.append({"experiment_id": item["experiment_id"], "reason": "safety_violation_recorded"})
            continue
        eligible.append(item)
    if len(eligible) < 2:
        return {
            "schema_version": 1,
            "state": "INCONCLUSIVE",
            "winner_experiment_id": None,
            "reasons": ["fewer_than_two_independently_verified_candidates"],
            "eligible_experiment_ids": [item["experiment_id"] for item in eligible],
            "rejected": rejected,
            "promotion_authority": "COMPARISON_ONLY",
        }

    reverse = contract["direction"] == "MAXIMIZE"
    ordered = sorted(
        eligible,
        key=lambda item: (
            -item["result_metrics"][primary] if reverse else item["result_metrics"][primary],
            item["experiment_id"],
        ),
    )
    winner, runner_up = ordered[0], ordered[1]
    if contract["direction"] == "MAXIMIZE":
        delta = winner["result_metrics"][primary] - runner_up["result_metrics"][primary]
    else:
        delta = runner_up["result_metrics"][primary] - winner["result_metrics"][primary]
    if delta <= contract["tolerance"]:
        state = "NO_CLEAR_WINNER"
        winner_id = None
        reasons = ["leading_candidates_within_measurement_tolerance"]
    else:
        state = "PROMOTE_CANDIDATE"
        winner_id = winner["experiment_id"]
        reasons = ["verified_primary_metric_exceeds_runner_up_beyond_tolerance"]

    return {
        "schema_version": 1,
        "state": state,
        "winner_experiment_id": winner_id,
        "primary_metric": primary,
        "direction": contract["direction"],
        "tolerance": contract["tolerance"],
        "observed_margin": delta,
        "reasons": reasons,
        "eligible_experiment_ids": [item["experiment_id"] for item in ordered],
        "rejected": rejected,
        "promotion_authority": "COMPARISON_ONLY",
        "independent_verification_still_required_for_product_promotion": True,
    }


def _experiment_kwargs(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise EngineeringScientistError("experiment records must be objects")
    required = {
        "project",
        "repository",
        "baseline_head_sha",
        "candidate_head_sha",
        "hypothesis_id",
        "hypothesis",
        "baseline_ref",
        "measurement_contract",
        "scope_refs",
        "evidence_refs",
        "resource_estimate",
        "action_guard_state",
    }
    missing = sorted(required - item.keys())
    if missing:
        raise EngineeringScientistError(f"experiment record missing fields: {missing}")
    return {
        key: item.get(key)
        for key in (
            "project",
            "repository",
            "baseline_head_sha",
            "candidate_head_sha",
            "hypothesis_id",
            "hypothesis",
            "baseline_ref",
            "measurement_contract",
            "scope_refs",
            "evidence_refs",
            "resource_estimate",
            "action_guard_state",
            "attempt",
            "parent_experiment_id",
            "parent_hypothesis_id",
            "state",
            "verification_state",
            "result_metrics",
            "verification_evidence_refs",
            "safety_violations",
        )
        if key in item
    }


def _normalize_capacity(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise EngineeringScientistError("capacity must be an object")
    slots_raw = raw.get("exclusive_resource_slots", {})
    if not isinstance(slots_raw, dict):
        raise EngineeringScientistError("exclusive_resource_slots must be an object")
    slots: dict[str, int] = {}
    for key, value in slots_raw.items():
        slots[_nonempty(key, "exclusive resource name")] = _nonnegative_int(value, f"exclusive_resource_slots.{key}")
    return {
        "compute_seconds": _nonnegative_number(raw.get("compute_seconds"), "capacity.compute_seconds"),
        "network_calls": _nonnegative_int(raw.get("network_calls"), "capacity.network_calls"),
        "write_operations": _nonnegative_int(raw.get("write_operations"), "capacity.write_operations"),
        "concurrency": _positive_int(raw.get("concurrency"), "capacity.concurrency"),
        "external_cost_usd": _nonnegative_number(raw.get("external_cost_usd", 0), "capacity.external_cost_usd"),
        "exclusive_resource_slots": slots,
    }


def _normalize_action(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise EngineeringScientistError("actions must contain objects")
    priority = raw.get("priority")
    if isinstance(priority, bool) or not isinstance(priority, int) or priority < 0 or priority > 6:
        raise EngineeringScientistError("action priority must be an integer from 0 through 6")
    guard = raw.get("action_guard_state")
    if guard not in ACTION_GUARD_STATES:
        raise EngineeringScientistError("action_guard_state is invalid")
    return {
        "action_id": _nonempty(raw.get("action_id"), "action_id"),
        "project": _nonempty(raw.get("project"), "project"),
        "repository": _nonempty(raw.get("repository"), "repository"),
        "priority": priority,
        "action_guard_state": guard,
        "resource_estimate": _normalize_resource_estimate(raw.get("resource_estimate")),
        "evidence_refs": _string_list(raw.get("evidence_refs", []), "evidence_refs"),
    }


def schedule_portfolio_resources(
    *, actions: list[dict[str, Any]], capacity: dict[str, Any]
) -> dict[str, Any]:
    """Schedule READY work fairly within each priority under explicit capacity.

    P0-P6 ordering remains authoritative. Fairness is round-robin by project
    only *within the same priority*, so a lower-priority project cannot preempt
    higher-priority work. The scheduler never executes the selected actions.
    """

    if not isinstance(actions, list):
        raise EngineeringScientistError("actions must be a list")
    normalized = [_normalize_action(item) for item in actions]
    cap = _normalize_capacity(capacity)
    remaining = {
        "compute_seconds": cap["compute_seconds"],
        "network_calls": cap["network_calls"],
        "write_operations": cap["write_operations"],
        "concurrency": cap["concurrency"],
        "external_cost_usd": cap["external_cost_usd"],
        "exclusive_resource_slots": dict(cap["exclusive_resource_slots"]),
    }
    scheduled: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []

    for item in normalized:
        if item["action_guard_state"] != "READY":
            deferred.append({
                "action_id": item["action_id"],
                "project": item["project"],
                "priority": item["priority"],
                "state": "BLOCKED_BY_ACTION_GUARD",
                "reason": f"Agent Action Guard state is {item['action_guard_state']}",
            })

    ready = [item for item in normalized if item["action_guard_state"] == "READY"]
    for priority in range(7):
        same_priority = [item for item in ready if item["priority"] == priority]
        queues: dict[str, deque[dict[str, Any]]] = {}
        for item in sorted(same_priority, key=lambda row: (row["project"], row["action_id"])):
            queues.setdefault(item["project"], deque()).append(item)
        projects = sorted(queues)
        while any(queues[project] for project in projects):
            made_progress = False
            for project in projects:
                if not queues[project]:
                    continue
                item = queues[project].popleft()
                fits, reason = _fits_capacity(item["resource_estimate"], remaining)
                if fits:
                    _consume_capacity(item["resource_estimate"], remaining)
                    scheduled.append({**item, "state": "SCHEDULED"})
                    made_progress = True
                else:
                    deferred.append({
                        "action_id": item["action_id"],
                        "project": item["project"],
                        "priority": item["priority"],
                        "state": "DEFERRED_RESOURCE",
                        "reason": reason,
                    })
            if not made_progress and all(not queues[project] for project in projects):
                break

    return {
        "schema_version": 1,
        "state": "SCHEDULED" if scheduled else "NO_ACTION_SCHEDULED",
        "capacity": cap,
        "remaining_capacity": remaining,
        "scheduled": scheduled,
        "deferred": sorted(deferred, key=lambda row: (row["priority"], row["project"], row["action_id"])),
        "fairness": "round_robin_by_project_within_same_priority",
        "priority_semantics": "P0-P6 remains authoritative; fairness never lets lower priority preempt higher priority",
        "execution_authority": "SCHEDULE_ONLY",
    }


def _fits_capacity(resource: dict[str, Any], remaining: dict[str, Any]) -> tuple[bool, str]:
    checks = (
        ("compute_seconds_estimate", "compute_seconds"),
        ("network_calls_estimate", "network_calls"),
        ("write_operations_estimate", "write_operations"),
        ("concurrency_estimate", "concurrency"),
    )
    for estimate_key, capacity_key in checks:
        if resource[estimate_key] > remaining[capacity_key]:
            return False, f"insufficient_{capacity_key}"
    cost = resource["external_cost_usd_estimate"] or 0.0
    if cost > remaining["external_cost_usd"]:
        return False, "insufficient_external_cost_budget"
    needed: dict[str, int] = defaultdict(int)
    for resource_name in resource["exclusive_resources"]:
        needed[resource_name] += 1
    for resource_name, count in needed.items():
        if count > remaining["exclusive_resource_slots"].get(resource_name, 0):
            return False, f"exclusive_resource_unavailable:{resource_name}"
    return True, "fits"


def _consume_capacity(resource: dict[str, Any], remaining: dict[str, Any]) -> None:
    remaining["compute_seconds"] -= resource["compute_seconds_estimate"]
    remaining["network_calls"] -= resource["network_calls_estimate"]
    remaining["write_operations"] -= resource["write_operations_estimate"]
    remaining["concurrency"] -= resource["concurrency_estimate"]
    remaining["external_cost_usd"] -= resource["external_cost_usd_estimate"] or 0.0
    for resource_name in resource["exclusive_resources"]:
        remaining["exclusive_resource_slots"][resource_name] -= 1


def _normalize_opportunity(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise EngineeringScientistError("opportunities must contain objects")
    priority = raw.get("priority")
    if isinstance(priority, bool) or not isinstance(priority, int) or priority < 0 or priority > 6:
        raise EngineeringScientistError("opportunity priority must be an integer from 0 through 6")
    signals = raw.get("measured_signals", [])
    if not isinstance(signals, list):
        raise EngineeringScientistError("measured_signals must be a list")
    normalized_signals: list[dict[str, Any]] = []
    for signal in signals:
        if not isinstance(signal, dict):
            raise EngineeringScientistError("measured signal must be an object")
        normalized_signals.append({
            "name": _nonempty(signal.get("name"), "measured_signal.name"),
            "value": _nonnegative_number(signal.get("value"), "measured_signal.value"),
            "evidence_ref": _nonempty(signal.get("evidence_ref"), "measured_signal.evidence_ref"),
        })
    return {
        "opportunity_id": _nonempty(raw.get("opportunity_id"), "opportunity_id"),
        "project": _nonempty(raw.get("project"), "project"),
        "repository": _nonempty(raw.get("repository"), "repository"),
        "source_type": _nonempty(raw.get("source_type"), "source_type"),
        "priority": priority,
        "scope_refs": _string_list(raw.get("scope_refs", []), "scope_refs"),
        "evidence_refs": _string_list(raw.get("evidence_refs", []), "evidence_refs"),
        "hypothesis": _nonempty(raw.get("hypothesis"), "hypothesis"),
        "measurement_contract": _normalize_measurement_contract(raw.get("measurement_contract")),
        "reversible": _bool(raw.get("reversible"), "reversible"),
        "bounded_scope": _bool(raw.get("bounded_scope"), "bounded_scope"),
        "deterministic_measurement": _bool(raw.get("deterministic_measurement"), "deterministic_measurement"),
        "active_coverage_refs": _string_list(raw.get("active_coverage_refs", []), "active_coverage_refs"),
        "measured_signals": normalized_signals,
    }


def sweep_opportunities(
    *, opportunities: list[dict[str, Any]], max_proposals: int = 5
) -> dict[str, Any]:
    """Convert evidence-backed gaps into bounded experiment proposals.

    This is downstream of triage/reinvestment. It does not invent opportunities
    and does not estimate ROI or probability. It prefers opportunities that can
    be tested reversibly with explicit deterministic measurements.
    """

    if not isinstance(opportunities, list):
        raise EngineeringScientistError("opportunities must be a list")
    limit = _positive_int(max_proposals, "max_proposals")
    normalized = [_normalize_opportunity(item) for item in opportunities]
    coverage_keys: dict[tuple[str, tuple[str, ...], str], str] = {}
    classified: list[dict[str, Any]] = []
    for item in sorted(normalized, key=lambda row: (row["priority"], row["opportunity_id"])):
        key = (
            item["repository"],
            tuple(item["scope_refs"]),
            item["measurement_contract"]["contract_id"],
        )
        reasons: list[str] = []
        if item["active_coverage_refs"]:
            state = "ALREADY_COVERED"
            reasons.append("active work or experiment already covers this opportunity")
        elif key in coverage_keys:
            state = "DUPLICATE"
            reasons.append(f"duplicates opportunity {coverage_keys[key]}")
        elif not item["evidence_refs"] and not item["measured_signals"]:
            state = "GATHER_EVIDENCE"
            reasons.append("no explicit evidence backs the opportunity")
        elif not item["scope_refs"]:
            state = "GATHER_EVIDENCE"
            reasons.append("scope is not bounded")
        elif not (item["reversible"] and item["bounded_scope"] and item["deterministic_measurement"]):
            state = "NOT_AUTONOMOUSLY_EXPERIMENTABLE"
            reasons.append("experiment is not simultaneously reversible, bounded, and deterministically measurable")
        else:
            state = "READY_FOR_BOUNDED_EXPERIMENT"
            reasons.append("evidence-backed reversible measurable opportunity")
            coverage_keys[key] = item["opportunity_id"]
        evidence_count = len(set(item["evidence_refs"]) | {s["evidence_ref"] for s in item["measured_signals"]})
        classified.append({
            **item,
            "state": state,
            "reasons": reasons,
            "evidence_count": evidence_count,
            "ranking_semantics": "priority then evidence breadth; not ROI, probability, severity, or percent complete",
        })

    ready = [item for item in classified if item["state"] == "READY_FOR_BOUNDED_EXPERIMENT"]
    ready.sort(key=lambda row: (row["priority"], -row["evidence_count"], row["project"], row["opportunity_id"]))
    selected_ids = {item["opportunity_id"] for item in ready[:limit]}
    results: list[dict[str, Any]] = []
    for item in classified:
        state = item["state"]
        if state == "READY_FOR_BOUNDED_EXPERIMENT":
            state = "PROPOSE_EXPERIMENT" if item["opportunity_id"] in selected_ids else "DEFERRED_BY_PROPOSAL_LIMIT"
        results.append({**item, "state": state})
    return {
        "schema_version": 1,
        "selected_opportunity_ids": [item["opportunity_id"] for item in ready[:limit]],
        "opportunities": sorted(results, key=lambda row: (row["priority"], row["project"], row["opportunity_id"])),
        "mode": "EVIDENCE_ONLY_EXPERIMENT_PROPOSAL",
        "execution_authority": "PROPOSAL_ONLY",
    }


def _normalize_lesson(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise EngineeringScientistError("lessons must contain objects")
    outcome = raw.get("verified_outcome")
    if outcome not in POSITIVE_PATTERN_OUTCOMES:
        raise EngineeringScientistError(f"verified_outcome must be one of {sorted(POSITIVE_PATTERN_OUTCOMES)}")
    return {
        "lesson_id": _nonempty(raw.get("lesson_id"), "lesson_id"),
        "repository": _nonempty(raw.get("repository"), "repository"),
        "project": _nonempty(raw.get("project"), "project"),
        "pattern_key": _nonempty(raw.get("pattern_key"), "pattern_key"),
        "mechanism": _nonempty(raw.get("mechanism"), "mechanism"),
        "applicability_conditions": _string_list(raw.get("applicability_conditions", []), "applicability_conditions"),
        "counterexamples": _string_list(raw.get("counterexamples", []), "counterexamples"),
        "evidence_refs": _string_list(raw.get("evidence_refs", []), "evidence_refs", allow_empty=False),
        "verified_outcome": outcome,
        "verified_outcome_ref": _nonempty(raw.get("verified_outcome_ref"), "verified_outcome_ref"),
        "domain_independent": _bool(raw.get("domain_independent"), "domain_independent"),
        "product_requirement_transfer": _bool(raw.get("product_requirement_transfer"), "product_requirement_transfer"),
    }


def distill_cross_repo_patterns(
    *, lessons: list[dict[str, Any]], min_repositories: int = 2, min_projects: int = 2
) -> dict[str, Any]:
    """Distill only repeatedly verified, domain-independent engineering patterns.

    Grouping is explicit by ``pattern_key``; this function never performs fuzzy
    semantic transfer. Product-specific requirements are never promoted.
    """

    if not isinstance(lessons, list):
        raise EngineeringScientistError("lessons must be a list")
    repo_threshold = _positive_int(min_repositories, "min_repositories")
    project_threshold = _positive_int(min_projects, "min_projects")
    normalized = [_normalize_lesson(item) for item in lessons]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for lesson in normalized:
        groups[lesson["pattern_key"]].append(lesson)
    results: list[dict[str, Any]] = []
    for pattern_key in sorted(groups):
        group = groups[pattern_key]
        rejected_domain = [item for item in group if not item["domain_independent"] or item["product_requirement_transfer"]]
        eligible = [item for item in group if item not in rejected_domain]
        mechanisms = {item["mechanism"] for item in eligible}
        repositories = sorted({item["repository"] for item in eligible})
        projects = sorted({item["project"] for item in eligible})
        if not eligible:
            state = "REJECT_DOMAIN_COUPLED"
            reason = "all evidence is domain-coupled or transfers product requirements"
            mechanism = None
        elif len(mechanisms) != 1:
            state = "CONFLICTING_MECHANISM"
            reason = "same pattern_key has conflicting mechanism descriptions"
            mechanism = None
        elif len(repositories) < repo_threshold or len(projects) < project_threshold:
            state = "LOCAL_OR_PROJECT_ONLY"
            reason = "insufficient independent repository/project evidence for portfolio promotion"
            mechanism = next(iter(mechanisms))
        else:
            state = "PORTFOLIO_PATTERN_CANDIDATE"
            reason = "repeated independently verified domain-independent evidence meets promotion threshold"
            mechanism = next(iter(mechanisms))
        results.append({
            "pattern_key": pattern_key,
            "state": state,
            "reason": reason,
            "mechanism": mechanism,
            "repositories": repositories,
            "projects": projects,
            "source_lesson_ids": sorted(item["lesson_id"] for item in eligible),
            "rejected_domain_lesson_ids": sorted(item["lesson_id"] for item in rejected_domain),
            "applicability_conditions": sorted({value for item in eligible for value in item["applicability_conditions"]}),
            "counterexamples": sorted({value for item in eligible for value in item["counterexamples"]}),
            "evidence_refs": sorted({ref for item in eligible for ref in item["evidence_refs"]} | {item["verified_outcome_ref"] for item in eligible}),
            "promotion_authority": "CANDIDATE_ONLY",
            "applicability_semantics": "all source applicability conditions remain constraints until separately verified",
        })
    return {
        "schema_version": 1,
        "min_repositories": repo_threshold,
        "min_projects": project_threshold,
        "patterns": results,
        "domain_rule": "never transfer product-specific requirements across projects",
        "grouping_rule": "exact explicit pattern_key only; no fuzzy semantic generalization",
    }


def validate_scientist_cycle(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the complete autonomous-engineering-scientist evidence loop."""

    if not isinstance(payload, dict):
        raise EngineeringScientistError("scientist cycle must be an object")
    if payload.get("schema_version") != 1:
        raise EngineeringScientistError("schema_version must be 1")
    cycle_id = _nonempty(payload.get("cycle_id"), "cycle_id")
    project = _nonempty(payload.get("project"), "project")
    repository = _nonempty(payload.get("repository"), "repository")
    baseline_head_sha = _sha(payload.get("baseline_head_sha"), "baseline_head_sha")
    stages = payload.get("stages")
    if not isinstance(stages, dict):
        raise EngineeringScientistError("stages must be an object")
    normalized_stages: dict[str, Any] = {}
    for stage in SCIENTIST_STAGES:
        item = stages.get(stage)
        if not isinstance(item, dict):
            raise EngineeringScientistError(f"stages.{stage} must be an object")
        state = item.get("state")
        if state not in {"COMPLETE", "BLOCKED", "NOT_APPLICABLE", "UNKNOWN"}:
            raise EngineeringScientistError(f"stages.{stage}.state is invalid")
        evidence_ref = item.get("evidence_ref")
        if state == "COMPLETE" and (not isinstance(evidence_ref, str) or not evidence_ref.strip()):
            raise EngineeringScientistError(f"stages.{stage} COMPLETE requires evidence_ref")
        if state == "BLOCKED" and not isinstance(item.get("reason"), str):
            raise EngineeringScientistError(f"stages.{stage} BLOCKED requires reason")
        normalized_stages[stage] = dict(item)

    action_guard_state = payload.get("action_guard_state")
    schedule_state = payload.get("schedule_state")
    if normalized_stages["EXECUTE"]["state"] == "COMPLETE":
        if action_guard_state != "READY":
            raise EngineeringScientistError("EXECUTE=COMPLETE requires action_guard_state=READY")
        if schedule_state != "SCHEDULED":
            raise EngineeringScientistError("EXECUTE=COMPLETE requires schedule_state=SCHEDULED")
    if normalized_stages["GENERALIZE"]["state"] == "COMPLETE" and normalized_stages["INDEPENDENT_VERIFY"]["state"] != "COMPLETE":
        raise EngineeringScientistError("GENERALIZE=COMPLETE requires INDEPENDENT_VERIFY=COMPLETE")
    if normalized_stages["COMPARE"]["state"] == "COMPLETE" and not payload.get("measurement_contract_ref"):
        raise EngineeringScientistError("COMPARE=COMPLETE requires measurement_contract_ref")
    closed = all(
        normalized_stages[stage]["state"] in {"COMPLETE", "NOT_APPLICABLE"}
        for stage in SCIENTIST_STAGES
    )
    return {
        "schema_version": 1,
        "cycle_id": cycle_id,
        "project": project,
        "repository": repository,
        "baseline_head_sha": baseline_head_sha,
        "stages": normalized_stages,
        "action_guard_state": action_guard_state,
        "schedule_state": schedule_state,
        "measurement_contract_ref": payload.get("measurement_contract_ref"),
        "cycle_closed": closed,
        "scientific_method": "observe -> hypothesize -> retrieve evidence -> bounded experiment -> estimate cost -> execute -> measure -> compare -> independently verify -> record -> generalize -> repeat",
        "execution_authority": "VALIDATION_ONLY",
    }
