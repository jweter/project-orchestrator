from project_orchestrator.jev_readiness import assess_free_readiness


def test_missing_account_evidence_fails_closed():
    result = assess_free_readiness(None, env={"TYPESAFE_API_KEY":"x","JEV_SHADOW_LIVE":"1"})
    assert result["ready_for_free_live_shadow"] is False
    assert "AUTHORITATIVE_ACCOUNT_EVIDENCE_MISSING" in result["reasons"]


def test_free_credit_requires_refill_explicitly_off():
    evidence={
        "source":"TYPESAFE_ACCOUNT_API",
        "promotional_credit_available":True,
        "purchased_credit_required":False,
        "automatic_refill_enabled":True,
    }
    result=assess_free_readiness(evidence, env={"TYPESAFE_API_KEY":"x","JEV_SHADOW_LIVE":"1"})
    assert result["ready_for_free_live_shadow"] is False
    assert "AUTOMATIC_REFILL_NOT_CONFIRMED_OFF" in result["reasons"]


def test_authoritative_free_evidence_and_runtime_gates_enable_readiness():
    evidence={
        "source":"TYPESAFE_ACCOUNT_EXPORT",
        "promotional_credit_available":True,
        "purchased_credit_required":False,
        "automatic_refill_enabled":False,
    }
    result=assess_free_readiness(evidence, env={"TYPESAFE_API_KEY":"x","JEV_SHADOW_LIVE":"1"})
    assert result["ready_for_free_live_shadow"] is True
    assert result["free_credit_available"] is True
    assert result["paid_spend_authorized"] is False


def test_key_value_is_never_returned():
    result=assess_free_readiness({}, env={"TYPESAFE_API_KEY":"super-secret","JEV_SHADOW_LIVE":"1"})
    assert "super-secret" not in str(result)
