from copy import deepcopy

from project_orchestrator.loop_audit import audit_loop_contract, load_loop_contract


def contract():
    return load_loop_contract("engineering/autonomy-loop-contract.json")


def codes(result):
    return {item["code"] for item in result["findings"]}


def test_authoritative_dowork_contract_is_green():
    result = audit_loop_contract(contract())
    assert result["status"] == "GREEN"
    assert result["finding_count"] == 0
    assert all(result["checks"].values())


def test_machine_decidable_exit_or_acceptance_is_required():
    value = contract()
    value["goal"]["machine_decidable"] = False
    result = audit_loop_contract(value)
    assert result["status"] == "RED"
    assert "GOAL_NOT_MACHINE_DECIDABLE" in codes(result)


def test_automated_verification_with_evidence_is_required():
    value = contract()
    value["verification"]["evidence_sources"] = []
    result = audit_loop_contract(value)
    assert "MISSING_VERIFICATION" in codes(result)


def test_goodhart_guard_requires_protected_invariants():
    value = contract()
    value["anti_goodhart"]["protected_invariants"] = []
    result = audit_loop_contract(value)
    assert "MISSING_ANTI_GOODHART_BOUNDARY" in codes(result)


def test_retry_budget_must_be_bounded_and_positive():
    value = contract()
    value["retries"]["max_substantive_repair_attempts_per_blocked_item_per_run"] = 0
    result = audit_loop_contract(value)
    assert "UNBOUNDED_RETRY" in codes(result)


def test_retry_exhaustion_requires_explicit_fallback():
    value = contract()
    value["retries"]["on_exhaustion"] = ""
    result = audit_loop_contract(value)
    assert "MISSING_RETRY_ESCALATION" in codes(result)


def test_builder_cannot_be_its_own_unconstrained_judge():
    value = contract()
    value["judge"]["independent_from_builder"] = False
    value["judge"]["builder_may_modify_acceptance"] = True
    result = audit_loop_contract(value)
    assert "JUDGE_NOT_INDEPENDENT" in codes(result)
    assert "BUILDER_CAN_REWRITE_ACCEPTANCE" in codes(result)


def test_material_ambiguity_must_escalate_or_defer():
    value = contract()
    value["ambiguity"]["material_ambiguity_action"] = "GUESS"
    result = audit_loop_contract(value)
    assert "AMBIGUITY_NOT_ESCALATED" in codes(result)


def test_stale_context_and_missing_evidence_fail_closed():
    value = contract()
    value["context"]["fresh_live_state_required"] = False
    value["context"]["missing_evidence_policy"] = "ASSUME_GREEN"
    result = audit_loop_contract(value)
    assert "STALE_CONTEXT_ALLOWED" in codes(result)
    assert "MISSING_FAIL_CLOSED_POLICY" in codes(result)


def test_automated_and_human_authority_must_be_disjoint():
    value = contract()
    duplicate = value["human_only_actions"] if "human_only_actions" in value else None
    assert duplicate is None
    value = deepcopy(value)
    overlap = value["authority"]["human_only_actions"][0]
    value["authority"]["automated_actions"].append(overlap)
    result = audit_loop_contract(value)
    assert "AUTHORITY_OVERLAP" in codes(result)


def test_product_reality_can_never_be_inferred():
    value = contract()
    value["authority"]["product_reality_may_be_inferred"] = True
    result = audit_loop_contract(value)
    assert "PRODUCT_REALITY_INFERENCE_ALLOWED" in codes(result)


def test_regulator_requires_dead_band_noop_semantics():
    value = contract()
    value["regulator"]["no_safe_change_action"] = "FORCE_CHANGE"
    value["regulator"]["unchanged_state_is_failure"] = True
    result = audit_loop_contract(value)
    assert "REGULATOR_NO_NOOP_SEMANTICS" in codes(result)
    assert "UNCHANGED_STATE_TREATED_AS_FAILURE" in codes(result)


def test_state_transitions_need_provenance_and_unknown_is_not_zero():
    value = contract()
    value["evidence"]["state_transitions_require_refs"] = False
    value["evidence"]["unknown_is_zero"] = True
    result = audit_loop_contract(value)
    assert "MISSING_EVIDENCE_PROVENANCE" in codes(result)
    assert "MISSING_FAIL_CLOSED_POLICY" in codes(result)
