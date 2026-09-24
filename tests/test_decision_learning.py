from project_orchestrator.decision_learning import compile_decision_learning, planning_hint
from project_orchestrator.learning_capacity import decision_feedback


def outcome(selector, *, cls="repair-selection", ref="ci:1", retries=0):
    return {
        "decision_class": cls, "state": "VERIFIED_ADVANCEMENT",
        "verification_ref": ref, "shadow_selected": selector == "shadow",
        "deterministic_selected": selector == "deterministic",
        "retries": retries, "verification_churn": 0, "elapsed_seconds": 10,
    }


def test_requires_verified_sample_floor_before_preference():
    learning = compile_decision_learning([outcome("shadow"), outcome("shadow")])
    hint = planning_hint(learning, "repair-selection")
    assert hint["evidence_state"] == "INSUFFICIENT_VERIFIED_EVIDENCE"
    assert hint["preferred_selector"] is None
    assert hint["authority"] == "ADVISORY_ONLY"


def test_verified_history_becomes_advisory_planning_hint():
    rows = [outcome("shadow"), outcome("shadow"), outcome("deterministic")]
    learning = compile_decision_learning(rows)
    hint = planning_hint(learning, "repair-selection")
    assert hint["evidence_state"] == "VERIFIED_ADVISORY_EVIDENCE"
    assert hint["preferred_selector"] == "JEV_SHADOW"
    assert hint["verified_samples"] == 3
    assert hint["may_change_legal_candidate_set"] is False
    assert hint["may_bypass_gates"] is False


def test_learning_capacity_closes_decision_feedback_loop_without_mutating_policy():
    result = decision_feedback(
        [outcome("deterministic"), outcome("deterministic"), outcome("shadow")],
        next_decision_class="repair-selection",
    )
    assert result["reuse_before_decision"]["preferred_selector"] == "DETERMINISTIC_FOREMAN"
    assert result["authority_preserved"] is True
    assert result["automatic_policy_mutation"] is False


def test_unverified_or_failed_rows_cannot_train_preference():
    rows = [outcome("shadow", ref=None), {
        "decision_class": "repair-selection", "state": "FAILED",
        "verification_ref": "ci:bad", "shadow_selected": True,
        "deterministic_selected": False,
    }]
    learning = compile_decision_learning(rows, minimum_verified_samples=1)
    assert planning_hint(learning, "repair-selection")["preferred_selector"] is None
