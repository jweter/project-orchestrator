from project_orchestrator.jev_account_evidence import collect_from_public_api_discovery, readiness_from_discovery


def test_public_api_discovery_never_claims_credit_balance():
    evidence=collect_from_public_api_discovery()
    assert evidence["public_api"]["credit_balance_endpoint"] is None
    assert evidence["promotional_credit_available"] is None
    assert evidence["authoritative_for_free_readiness"] is False


def test_documented_api_surface_fails_closed_even_with_key_and_live_flag():
    result=readiness_from_discovery(env={"TYPESAFE_API_KEY":"x","JEV_SHADOW_LIVE":"1"})
    assert result["readiness"]["ready_for_free_live_shadow"] is False
    assert result["next_action"] == "OBTAIN_ACCOUNT_EXPORT_OR_PROVIDER_ACCOUNT_ENDPOINT"
    assert result["readiness"]["paid_spend_authorized"] is False


def test_discovery_requires_no_billable_network_call():
    evidence=collect_from_public_api_discovery()
    assert evidence["network_call_required"] is False
    assert evidence["paid_spend_authorized"] is False
