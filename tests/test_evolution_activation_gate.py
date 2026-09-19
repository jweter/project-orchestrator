from scripts.evolution_activation_gate import validate_activation


def test_current_green_activation_is_allowed() -> None:
    state = {"enabled": True, "reason": "READINESS_PASS", "heads": {"repo": "abc"}}
    assert validate_activation(state, {"repo": "abc"}) == (True, "ACTIVATION_CURRENT")


def test_stale_head_is_rejected() -> None:
    state = {"enabled": True, "reason": "READINESS_PASS", "heads": {"repo": "abc"}}
    assert validate_activation(state, {"repo": "def"}) == (False, "ACTIVATION_HEADS_STALE")


def test_disabled_or_missing_evidence_is_rejected() -> None:
    assert validate_activation({"enabled": False, "heads": {"repo": "abc"}}, {"repo": "abc"})[0] is False
    assert validate_activation({"enabled": True, "reason": "READINESS_PASS", "heads": {}}, {})[0] is False

def test_product_head_drift_quarantines_only_affected_product():
    from scripts.evolution_activation_gate import validate_products
    state={"products":{
        "knowledge-engine":{"enabled":True,"repositories":{"jweter/core":"aaa","jweter/web":"bbb"}},
        "rocksmith":{"enabled":True,"repositories":{"jweter/rock":"ccc"}},
    }}
    current={"jweter/core":"changed","jweter/web":"bbb","jweter/rock":"ccc"}
    result=validate_products(state,current)
    assert result["knowledge-engine"]=={"allowed":False,"reason":"PRODUCT_HEADS_STALE"}
    assert result["rocksmith"]=={"allowed":True,"reason":"PRODUCT_CURRENT"}

def test_activation_survives_unrelated_product_head_drift():
    from scripts.evolution_activation_gate import validate_activation
    state={"reason":"READINESS_PASS","products":{
        "knowledge-engine":{"enabled":True,"repositories":{"jweter/core":"aaa"}},
        "rocksmith":{"enabled":True,"repositories":{"jweter/rock":"ccc"}},
    }}
    assert validate_activation(state,{"jweter/core":"changed","jweter/rock":"ccc"})==(True,"ACTIVATION_CURRENT")
