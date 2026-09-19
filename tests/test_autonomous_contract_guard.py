from scripts.autonomous_contract_guard import (
    KNOWN_POLICY_WEAKENING_FINGERPRINT,
    assess_policy_contract,
)

BASE={
    "states":["READY","GATHER_CONTEXT","APPROVAL_REQUIRED","FORBIDDEN"],
    "required_context":["exact_head_sha","evidence_refs"],
    "failure_or_retry_additional_requirement":"exact_error_ref",
    "default_policy":{"max_concurrency_without_approval":4,"hard_concurrency":12},
    "reserved_human_rule":"reserved human requires approval",
    "hard_cap_rule":"hard cap is forbidden",
    "execution_authority":"GATE_ONLY",
    "existing_gates_preserved":["fresh PR CI","security"],
}
def test_unchanged_policy_contract_passes():
    assert assess_policy_contract("engineering/agent-action-guard.json",BASE,dict(BASE)) is None

def test_pr174_style_deleted_contract_keys_is_rejected():
    weak={"states":BASE["states"]}
    failure=assess_policy_contract("engineering/agent-action-guard.json",BASE,weak)
    assert failure.startswith(KNOWN_POLICY_WEAKENING_FINGERPRINT)
    assert "deleted_keys" in failure

def test_required_context_cannot_be_reduced():
    candidate={**BASE,"required_context":["exact_head_sha"]}
    failure=assess_policy_contract("engineering/agent-action-guard.json",BASE,candidate)
    assert "reduced_set:required_context" in failure

def test_gate_only_boundary_cannot_change():
    candidate={**BASE,"execution_authority":"EXECUTE"}
    failure=assess_policy_contract("engineering/agent-action-guard.json",BASE,candidate)
    assert "changed_contract:execution_authority" in failure

def test_autonomous_actor_cannot_change_default_policy_thresholds():
    candidate={**BASE,"default_policy":{"max_concurrency_without_approval":8,"hard_concurrency":12}}
    failure=assess_policy_contract("engineering/agent-action-guard.json",BASE,candidate)
    assert "changed_default_policy" in failure
