from __future__ import annotations

from typing import Any

from .area_learning import retrieve_learning
from .exponential_engine import capacity_decision, extract_learning_candidate
from .decision_learning import compile_decision_learning, planning_hint


def close_learning_loop(
    result: dict[str, Any],
    memory: dict[str, Any],
    *,
    next_repository: str,
    next_paths: list[str],
    next_summary: str = "",
) -> dict[str, Any]:
    """Extract verified capability metadata and query it before the next mutation.

    Persistence remains the learning-memory authority; this function never writes
    unverified worker payloads into memory and never treats similarity as authority.
    """
    candidate = extract_learning_candidate(result)
    retrieval = retrieve_learning(
        memory,
        repository=next_repository,
        changed_paths=next_paths,
        change_summary=next_summary,
    )
    return {
        "schema_version": 1,
        "learning_candidate": candidate,
        "learning_extraction_state": "READY_FOR_VERIFIED_PERSISTENCE" if candidate else "NO_VERIFIED_REUSABLE_CAPABILITY",
        "reuse_before_mutation": retrieval,
        "raw_worker_payload_persisted": False,
        "authority_preserved": True,
    }


def decision_feedback(attributions: list[dict[str, Any]], *,
                      next_decision_class: str,
                      minimum_verified_samples: int = 3) -> dict[str, Any]:
    """Compile verified outcome evidence and query it before the next decision."""
    learning = compile_decision_learning(
        attributions, minimum_verified_samples=minimum_verified_samples
    )
    return {
        "schema_version": 1,
        "decision_learning": learning,
        "reuse_before_decision": planning_hint(learning, next_decision_class),
        "authority_preserved": True,
        "automatic_policy_mutation": False,
    }


def adaptive_capacity_feedback(
    observations: list[dict[str, Any]],
    policy: dict[str, Any],
    *,
    current_capacity: int,
) -> dict[str, Any]:
    """Convert comparable measured windows into a bounded capacity decision."""
    if not isinstance(observations, list):
        raise ValueError("observations must be a list")
    measured = [item for item in observations if isinstance(item, dict) and item.get("comparable") is True]
    evidence: dict[str, Any] = {
        "current_capacity": current_capacity,
        "comparable_evidence_windows": len(measured),
    }
    if measured:
        evidence.update({
            "all_sampled_required_verification_green": all(item.get("required_verification_green") is True for item in measured),
            "merge_race_count_zero_when_measured": all(item.get("merge_race_count") == 0 for item in measured if item.get("merge_race_count") is not None) if all(item.get("merge_race_count") is not None for item in measured) else None,
            "no_measured_post_merge_correctness_hotfix": all(item.get("post_merge_correctness_hotfix") is False for item in measured if item.get("post_merge_correctness_hotfix") is not None) if all(item.get("post_merge_correctness_hotfix") is not None for item in measured) else None,
            "no_measured_repeat_failure_regression": all(item.get("repeat_failure_regression") is False for item in measured if item.get("repeat_failure_regression") is not None) if all(item.get("repeat_failure_regression") is not None for item in measured) else None,
            "reconciliation_conflict_rate_increase": any(item.get("reconciliation_conflict_rate_increase") is True for item in measured) if all(item.get("reconciliation_conflict_rate_increase") is not None for item in measured) else None,
        })
    decision = capacity_decision(evidence, policy)
    return {
        "schema_version": 1,
        "measured_windows": len(measured),
        "ignored_unknown_or_noncomparable_windows": len(observations) - len(measured),
        "evidence": evidence,
        "decision": decision.__dict__,
        "automatic_change_allowed": decision.action in {"INCREASE", "DECREASE"},
        "quality_gates_override_throughput": True,
    }
