from __future__ import annotations

import pytest

from project_orchestrator.emerging_compute import (
    EmergingComputeError,
    assess_compute_benchmark_result,
    evaluate_compute_substrates,
)


def problem(*, verified=True):
    return {
        "problem_id": "ke-hybrid-optimization-1",
        "project": "knowledge-engine",
        "repository": "jweter/knowledge-engine-core",
        "objective": "compare alternative compute substrates under one research benchmark",
        "measurement_contract_ref": "contract:hybrid-v1",
        "classical_baseline": {
            "substrate_class": "CLASSICAL_CPU",
            "verified": verified,
            "evidence_refs": ["baseline:cpu-v1"] if verified else [],
        },
    }


def candidate(**overrides):
    value = {
        "candidate_id": "qsim-1",
        "substrate_class": "QUANTUM_SIMULATOR",
        "mechanism": "simulate a bounded variational candidate under the same benchmark",
        "measurement_contract_ref": "contract:hybrid-v1",
        "problem_fit": "HIGH",
        "scientific_validity": "PLAUSIBLE",
        "evidence_refs": ["paper:method", "mapping:problem"],
        "action_guard_state": "READY",
        "estimated_cost_usd": 0,
        "paid_external": False,
        "stochastic": True,
        "replicates_or_shots": 100,
        "reproducibility_ref": "protocol:seed-shots-v1",
        "simulator_applicable": True,
        "simulator_verified": True,
        "environment_identity_ref": "env:local-simulator-v1",
        "operational_complexity": "MEDIUM",
        "project_value": "HIGH",
        "claimed_general_quantum_advantage": False,
    }
    value.update(overrides)
    return value


def test_verified_classical_baseline_is_required():
    result = evaluate_compute_substrates(problem=problem(verified=False), candidates=[candidate()])
    assert result["candidates"][0]["state"] == "BASELINE_REQUIRED"
    assert result["dedicated_quantum_lab_authorized"] is False


def test_quantum_hardware_uses_simulator_first_when_applicable():
    qpu = candidate(
        candidate_id="qpu-1",
        substrate_class="QUANTUM_HARDWARE",
        simulator_verified=False,
        paid_external=True,
        estimated_cost_usd=10,
        environment_identity_ref="qpu:provider-device-calibration",
    )
    result = evaluate_compute_substrates(problem=problem(), candidates=[qpu])
    assert result["candidates"][0]["state"] == "SIMULATE_FIRST"


def test_paid_hardware_requires_approval_after_simulator_gate():
    qpu = candidate(
        candidate_id="qpu-1",
        substrate_class="QUANTUM_HARDWARE",
        simulator_verified=True,
        paid_external=True,
        estimated_cost_usd=10,
        environment_identity_ref="qpu:provider-device-calibration",
    )
    result = evaluate_compute_substrates(problem=problem(), candidates=[qpu])
    assert result["candidates"][0]["state"] == "APPROVAL_REQUIRED"


def test_stochastic_candidate_requires_reproducibility_contract():
    result = evaluate_compute_substrates(
        problem=problem(),
        candidates=[candidate(reproducibility_ref=None)],
    )
    assert result["candidates"][0]["state"] == "GATHER_EVIDENCE"


def test_environment_bound_candidate_requires_environment_identity():
    result = evaluate_compute_substrates(
        problem=problem(),
        candidates=[candidate(environment_identity_ref=None)],
    )
    assert result["candidates"][0]["state"] == "GATHER_EVIDENCE"


def test_scientifically_unsupported_candidate_is_not_justified():
    result = evaluate_compute_substrates(
        problem=problem(),
        candidates=[candidate(scientific_validity="UNSUPPORTED")],
    )
    assert result["candidates"][0]["state"] == "NOT_JUSTIFIED"


def test_low_problem_fit_is_rejected_even_when_other_gates_are_green():
    result = evaluate_compute_substrates(problem=problem(), candidates=[candidate(problem_fit="LOW")])
    assert result["candidates"][0]["state"] == "NOT_JUSTIFIED"


def test_free_quantum_simulator_can_become_bounded_experiment_only():
    result = evaluate_compute_substrates(problem=problem(), candidates=[candidate()])
    decision = result["candidates"][0]
    assert decision["state"] == "ELIGIBLE_FOR_BOUNDED_EXPERIMENT"
    assert decision["execution_authority"] == "EVALUATION_ONLY"
    assert decision["runtime_dependency_authorized"] is False


def test_general_quantum_advantage_claim_is_never_inferred_from_candidate():
    result = evaluate_compute_substrates(
        problem=problem(),
        candidates=[candidate(claimed_general_quantum_advantage=True)],
    )
    assert result["candidates"][0]["claim_state"] == "GENERAL_QUANTUM_ADVANTAGE_CLAIM_REJECTED"


def test_measurement_contract_mismatch_is_not_justified():
    result = evaluate_compute_substrates(
        problem=problem(),
        candidates=[candidate(measurement_contract_ref="contract:other")],
    )
    assert result["candidates"][0]["state"] == "NOT_JUSTIFIED"


def test_duplicate_candidate_ids_fail_closed():
    with pytest.raises(EmergingComputeError, match="candidate_id"):
        evaluate_compute_substrates(problem=problem(), candidates=[candidate(), candidate()])


def test_benchmark_superiority_requires_repeatability_and_independent_verification():
    result = assess_compute_benchmark_result(
        baseline_metric=100,
        candidate_metric=80,
        direction="MINIMIZE",
        tolerance=1,
        independent_verification_refs=["ci:green"],
        repeatability_refs=["runs:five-seeds"],
        minimum_independent_verifications=2,
    )
    assert result["state"] == "PROMISING_NOT_INDEPENDENTLY_VERIFIED"
    assert result["general_quantum_advantage_claim"] == "NOT_ESTABLISHED_BY_LOCAL_BENCHMARK"


def test_benchmark_superiority_can_be_verified_locally_without_overclaiming():
    result = assess_compute_benchmark_result(
        baseline_metric=100,
        candidate_metric=80,
        direction="MINIMIZE",
        tolerance=1,
        independent_verification_refs=["ci:green", "lvn:verified"],
        repeatability_refs=["runs:five-seeds"],
    )
    assert result["state"] == "BENCHMARK_SUPERIORITY_VERIFIED"
    assert result["observed_margin"] == 20
    assert result["general_quantum_advantage_claim"] == "NOT_ESTABLISHED_BY_LOCAL_BENCHMARK"


def test_no_measured_advantage_is_recorded_not_promoted():
    result = assess_compute_benchmark_result(
        baseline_metric=100,
        candidate_metric=99.5,
        direction="MINIMIZE",
        tolerance=1,
        independent_verification_refs=["ci:green", "lvn:verified"],
        repeatability_refs=["runs:five-seeds"],
    )
    assert result["state"] == "NO_MEASURED_ADVANTAGE"
