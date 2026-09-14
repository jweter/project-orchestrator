from __future__ import annotations

from typing import Any


class EmergingComputeError(ValueError):
    """Raised when emerging-compute evidence violates the evaluation contract."""


SUBSTRATE_CLASSES = {
    "CLASSICAL_CPU",
    "CLASSICAL_GPU",
    "CLASSICAL_ACCELERATOR",
    "QUANTUM_INSPIRED",
    "QUANTUM_SIMULATOR",
    "QUANTUM_HARDWARE",
    "PHOTONIC",
    "NEUROMORPHIC",
    "OTHER_EXPERIMENTAL",
}
ACTION_GUARD_STATES = {"READY", "GATHER_CONTEXT", "APPROVAL_REQUIRED", "FORBIDDEN"}
FIT_STATES = {"HIGH", "MEDIUM", "LOW", "NONE"}
VALIDITY_STATES = {"SUPPORTED", "PLAUSIBLE", "UNKNOWN", "UNSUPPORTED"}
COMPLEXITY_STATES = {"LOW", "MEDIUM", "HIGH"}
VALUE_STATES = {"LOW", "MEDIUM", "HIGH"}
QUANTUM_CLASSES = {"QUANTUM_INSPIRED", "QUANTUM_SIMULATOR", "QUANTUM_HARDWARE"}
ENVIRONMENT_BOUND_CLASSES = {"QUANTUM_SIMULATOR", "QUANTUM_HARDWARE", "PHOTONIC", "NEUROMORPHIC"}

DEFAULT_POLICY: dict[str, Any] = {
    "max_external_cost_usd_without_approval": 0.0,
    "hardware_requires_simulator_first_when_applicable": True,
    "minimum_stochastic_replicates_or_shots": 1,
    "minimum_independent_verifications_for_benchmark_superiority": 2,
    "reject_unmarked_general_quantum_advantage_claims": True,
}


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EmergingComputeError(f"{name} must be a non-empty string")
    return value.strip()


def _bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise EmergingComputeError(f"{name} must be boolean")
    return value


def _nonnegative_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise EmergingComputeError(f"{name} must be a non-negative number")
    return float(value)


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise EmergingComputeError(f"{name} must be a positive integer")
    return value


def _string_list(value: Any, name: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise EmergingComputeError(f"{name} must be a list of non-empty strings")
    normalized = sorted({item.strip() for item in value})
    if not allow_empty and not normalized:
        raise EmergingComputeError(f"{name} must not be empty")
    return normalized


def _choice(value: Any, name: str, allowed: set[str]) -> str:
    text = _nonempty(value, name)
    if text not in allowed:
        raise EmergingComputeError(f"{name} must be one of {sorted(allowed)}")
    return text


def _policy(raw: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(DEFAULT_POLICY)
    if raw:
        if not isinstance(raw, dict):
            raise EmergingComputeError("policy must be an object")
        merged.update(raw)
    merged["max_external_cost_usd_without_approval"] = _nonnegative_number(
        merged["max_external_cost_usd_without_approval"],
        "policy.max_external_cost_usd_without_approval",
    )
    merged["hardware_requires_simulator_first_when_applicable"] = _bool(
        merged["hardware_requires_simulator_first_when_applicable"],
        "policy.hardware_requires_simulator_first_when_applicable",
    )
    merged["minimum_stochastic_replicates_or_shots"] = _positive_int(
        merged["minimum_stochastic_replicates_or_shots"],
        "policy.minimum_stochastic_replicates_or_shots",
    )
    merged["minimum_independent_verifications_for_benchmark_superiority"] = _positive_int(
        merged["minimum_independent_verifications_for_benchmark_superiority"],
        "policy.minimum_independent_verifications_for_benchmark_superiority",
    )
    merged["reject_unmarked_general_quantum_advantage_claims"] = _bool(
        merged["reject_unmarked_general_quantum_advantage_claims"],
        "policy.reject_unmarked_general_quantum_advantage_claims",
    )
    return merged


def _normalize_problem(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise EmergingComputeError("problem must be an object")
    baseline = raw.get("classical_baseline")
    if not isinstance(baseline, dict):
        raise EmergingComputeError("problem.classical_baseline must be an object")
    baseline_class = _choice(
        baseline.get("substrate_class"),
        "problem.classical_baseline.substrate_class",
        {"CLASSICAL_CPU", "CLASSICAL_GPU", "CLASSICAL_ACCELERATOR"},
    )
    baseline_verified = _bool(baseline.get("verified", False), "problem.classical_baseline.verified")
    baseline_evidence = _string_list(
        baseline.get("evidence_refs", []),
        "problem.classical_baseline.evidence_refs",
    )
    if baseline_verified and not baseline_evidence:
        raise EmergingComputeError("verified classical baseline requires evidence_refs")
    return {
        "problem_id": _nonempty(raw.get("problem_id"), "problem.problem_id"),
        "project": _nonempty(raw.get("project"), "problem.project"),
        "repository": _nonempty(raw.get("repository"), "problem.repository"),
        "objective": _nonempty(raw.get("objective"), "problem.objective"),
        "measurement_contract_ref": _nonempty(
            raw.get("measurement_contract_ref"), "problem.measurement_contract_ref"
        ),
        "classical_baseline": {
            "substrate_class": baseline_class,
            "verified": baseline_verified,
            "evidence_refs": baseline_evidence,
        },
    }


def _normalize_candidate(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise EmergingComputeError("candidate must be an object")
    substrate_class = _choice(raw.get("substrate_class"), "candidate.substrate_class", SUBSTRATE_CLASSES)
    estimated_cost = raw.get("estimated_cost_usd")
    if estimated_cost is not None:
        estimated_cost = _nonnegative_number(estimated_cost, "candidate.estimated_cost_usd")
    environment_identity_ref = raw.get("environment_identity_ref")
    if environment_identity_ref is not None:
        environment_identity_ref = _nonempty(environment_identity_ref, "candidate.environment_identity_ref")
    reproducibility_ref = raw.get("reproducibility_ref")
    if reproducibility_ref is not None:
        reproducibility_ref = _nonempty(reproducibility_ref, "candidate.reproducibility_ref")
    return {
        "candidate_id": _nonempty(raw.get("candidate_id"), "candidate.candidate_id"),
        "substrate_class": substrate_class,
        "mechanism": _nonempty(raw.get("mechanism"), "candidate.mechanism"),
        "measurement_contract_ref": _nonempty(
            raw.get("measurement_contract_ref"), "candidate.measurement_contract_ref"
        ),
        "problem_fit": _choice(raw.get("problem_fit"), "candidate.problem_fit", FIT_STATES),
        "scientific_validity": _choice(
            raw.get("scientific_validity"), "candidate.scientific_validity", VALIDITY_STATES
        ),
        "evidence_refs": _string_list(raw.get("evidence_refs", []), "candidate.evidence_refs"),
        "action_guard_state": _choice(
            raw.get("action_guard_state"), "candidate.action_guard_state", ACTION_GUARD_STATES
        ),
        "estimated_cost_usd": estimated_cost,
        "paid_external": _bool(raw.get("paid_external", False), "candidate.paid_external"),
        "stochastic": _bool(raw.get("stochastic", False), "candidate.stochastic"),
        "replicates_or_shots": _positive_int(
            raw.get("replicates_or_shots", 1), "candidate.replicates_or_shots"
        ),
        "reproducibility_ref": reproducibility_ref,
        "simulator_applicable": _bool(
            raw.get("simulator_applicable", False), "candidate.simulator_applicable"
        ),
        "simulator_verified": _bool(raw.get("simulator_verified", False), "candidate.simulator_verified"),
        "environment_identity_ref": environment_identity_ref,
        "operational_complexity": _choice(
            raw.get("operational_complexity", "MEDIUM"),
            "candidate.operational_complexity",
            COMPLEXITY_STATES,
        ),
        "project_value": _choice(raw.get("project_value", "MEDIUM"), "candidate.project_value", VALUE_STATES),
        "claimed_general_quantum_advantage": _bool(
            raw.get("claimed_general_quantum_advantage", False),
            "candidate.claimed_general_quantum_advantage",
        ),
    }


def evaluate_compute_substrates(
    *,
    problem: dict[str, Any],
    candidates: list[dict[str, Any]],
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate emerging-compute candidates without granting execution authority.

    The evaluator deliberately treats every non-baseline substrate as an
    experimental instrument. It never infers broad quantum advantage from a
    local benchmark and never adds a runtime dependency to a product.
    """

    normalized_problem = _normalize_problem(problem)
    if not isinstance(candidates, list) or not candidates:
        raise EmergingComputeError("candidates must be a non-empty list")
    normalized_candidates = [_normalize_candidate(item) for item in candidates]
    ids = [item["candidate_id"] for item in normalized_candidates]
    if len(ids) != len(set(ids)):
        raise EmergingComputeError("candidate_id values must be unique")
    active_policy = _policy(policy)
    baseline = normalized_problem["classical_baseline"]
    decisions: list[dict[str, Any]] = []

    for candidate in normalized_candidates:
        reasons: list[str] = []
        state = "ELIGIBLE_FOR_BOUNDED_EXPERIMENT"

        if candidate["action_guard_state"] == "FORBIDDEN":
            state = "FORBIDDEN"
            reasons.append("agent_action_guard_forbidden")
        elif candidate["measurement_contract_ref"] != normalized_problem["measurement_contract_ref"]:
            state = "NOT_JUSTIFIED"
            reasons.append("measurement_contract_mismatch")
        elif not baseline["verified"]:
            state = "BASELINE_REQUIRED"
            reasons.append("verified_classical_baseline_missing")
        elif candidate["problem_fit"] == "NONE":
            state = "NOT_JUSTIFIED"
            reasons.append("no_problem_fit")
        elif candidate["scientific_validity"] == "UNSUPPORTED":
            state = "NOT_JUSTIFIED"
            reasons.append("scientific_mechanism_unsupported")
        elif candidate["scientific_validity"] == "UNKNOWN" or not candidate["evidence_refs"]:
            state = "GATHER_EVIDENCE"
            reasons.append("scientific_or_problem_evidence_incomplete")
        elif (
            candidate["substrate_class"] == "QUANTUM_HARDWARE"
            and active_policy["hardware_requires_simulator_first_when_applicable"]
            and candidate["simulator_applicable"]
            and not candidate["simulator_verified"]
        ):
            state = "SIMULATE_FIRST"
            reasons.append("applicable_simulator_not_verified")
        elif candidate["stochastic"] and (
            candidate["reproducibility_ref"] is None
            or candidate["replicates_or_shots"] < active_policy["minimum_stochastic_replicates_or_shots"]
        ):
            state = "GATHER_EVIDENCE"
            reasons.append("stochastic_reproducibility_contract_incomplete")
        elif candidate["substrate_class"] in ENVIRONMENT_BOUND_CLASSES and candidate["environment_identity_ref"] is None:
            state = "GATHER_EVIDENCE"
            reasons.append("environment_identity_missing")
        elif candidate["action_guard_state"] == "GATHER_CONTEXT":
            state = "GATHER_EVIDENCE"
            reasons.append("agent_action_guard_requires_context")
        elif candidate["action_guard_state"] == "APPROVAL_REQUIRED":
            state = "APPROVAL_REQUIRED"
            reasons.append("agent_action_guard_requires_approval")
        elif candidate["paid_external"] and (
            candidate["estimated_cost_usd"] is None
            or candidate["estimated_cost_usd"] > active_policy["max_external_cost_usd_without_approval"]
        ):
            state = "APPROVAL_REQUIRED"
            reasons.append("paid_external_compute_requires_approval")
        else:
            reasons.append("bounded_experiment_requirements_satisfied")

        if candidate["problem_fit"] == "LOW" and state == "ELIGIBLE_FOR_BOUNDED_EXPERIMENT":
            state = "NOT_JUSTIFIED"
            reasons = ["problem_fit_too_low_for_portfolio_experiment"]

        claim_state = "NO_GENERAL_ADVANTAGE_CLAIM"
        if candidate["substrate_class"] in QUANTUM_CLASSES:
            claim_state = "GENERAL_QUANTUM_ADVANTAGE_NOT_ESTABLISHED"
            if candidate["claimed_general_quantum_advantage"]:
                claim_state = "GENERAL_QUANTUM_ADVANTAGE_CLAIM_REJECTED"

        decisions.append(
            {
                **candidate,
                "state": state,
                "reasons": reasons,
                "claim_state": claim_state,
                "execution_authority": "EVALUATION_ONLY",
                "runtime_dependency_authorized": False,
            }
        )

    return {
        "schema_version": 1,
        "problem": normalized_problem,
        "policy": active_policy,
        "candidates": decisions,
        "portfolio_rule": "classical baseline -> bounded candidate experiment -> independent verification -> evidence-based adoption",
        "dedicated_quantum_lab_authorized": False,
        "execution_authority": "EVALUATION_ONLY",
    }


def assess_compute_benchmark_result(
    *,
    baseline_metric: float,
    candidate_metric: float,
    direction: str,
    tolerance: float,
    independent_verification_refs: list[str],
    repeatability_refs: list[str],
    minimum_independent_verifications: int = 2,
    safety_violations: list[str] | None = None,
) -> dict[str, Any]:
    """Assess local benchmark superiority without making a general advantage claim."""

    baseline = _nonnegative_number(baseline_metric, "baseline_metric")
    candidate = _nonnegative_number(candidate_metric, "candidate_metric")
    if direction not in {"MINIMIZE", "MAXIMIZE"}:
        raise EmergingComputeError("direction must be MINIMIZE or MAXIMIZE")
    tolerance_value = _nonnegative_number(tolerance, "tolerance")
    verifications = _string_list(independent_verification_refs, "independent_verification_refs")
    repeatability = _string_list(repeatability_refs, "repeatability_refs")
    minimum = _positive_int(minimum_independent_verifications, "minimum_independent_verifications")
    violations = _string_list(safety_violations or [], "safety_violations")

    if direction == "MINIMIZE":
        margin = baseline - candidate
    else:
        margin = candidate - baseline

    if violations:
        state = "INCONCLUSIVE_SAFETY"
        reasons = ["safety_violation_recorded"]
    elif margin <= tolerance_value:
        state = "NO_MEASURED_ADVANTAGE"
        reasons = ["candidate_does_not_exceed_baseline_beyond_tolerance"]
    elif not repeatability:
        state = "PROMISING_NOT_REPEATABLE"
        reasons = ["repeatability_evidence_missing"]
    elif len(verifications) < minimum:
        state = "PROMISING_NOT_INDEPENDENTLY_VERIFIED"
        reasons = ["independent_verification_threshold_not_met"]
    else:
        state = "BENCHMARK_SUPERIORITY_VERIFIED"
        reasons = ["candidate_exceeds_classical_baseline_beyond_tolerance_with_repeatable_independent_evidence"]

    return {
        "schema_version": 1,
        "state": state,
        "observed_margin": margin,
        "direction": direction,
        "tolerance": tolerance_value,
        "independent_verification_count": len(verifications),
        "repeatability_evidence_count": len(repeatability),
        "reasons": reasons,
        "general_quantum_advantage_claim": "NOT_ESTABLISHED_BY_LOCAL_BENCHMARK",
        "adoption_authority": "BENCHMARK_EVIDENCE_ONLY",
    }
