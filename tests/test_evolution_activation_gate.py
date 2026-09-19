from scripts.evolution_activation_gate import validate_activation


def test_current_green_activation_is_allowed() -> None:
    state = {"schema_version": 2, "enabled": True, "reason": "READINESS_PASS", "products": {"test": {"enabled": True, "repositories": {"repo": "abc"}}}}
    assert validate_activation(state, {"repo": "abc"}) == (True, "ACTIVATION_CURRENT")


def test_stale_head_is_rejected() -> None:
    state = {"schema_version": 2, "enabled": True, "reason": "READINESS_PASS", "products": {"test": {"enabled": True, "repositories": {"repo": "abc"}}}}
    assert validate_activation(state, {"repo": "def"}) == (False, "NO_CURRENT_PRODUCTS")


def test_disabled_or_missing_evidence_is_rejected() -> None:
    assert validate_activation({"schema_version":2,"enabled": False, "heads": {"repo": "abc"}}, {"repo": "abc"})[0] is False
    assert validate_activation({"schema_version":2,"enabled": True, "reason": "READINESS_PASS", "heads": {}}, {})[0] is False

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
    state={"schema_version":2,"reason":"READINESS_PASS","products":{
        "knowledge-engine":{"enabled":True,"repositories":{"jweter/core":"aaa"}},
        "rocksmith":{"enabled":True,"repositories":{"jweter/rock":"ccc"}},
    }}
    assert validate_activation(state,{"jweter/core":"changed","jweter/rock":"ccc"})==(True,"ACTIVATION_CURRENT")

def test_schema_v1_activation_is_rejected():
    state={"schema_version":1,"reason":"READINESS_PASS","products":{
        "rocksmith":{"enabled":True,"repositories":{"jweter/rock":"abc"}}}}
    assert validate_activation(state,{"jweter/rock":"abc"})==(False,"ACTIVATION_SCHEMA_UNSUPPORTED")

def test_contract_rejects_incomplete_product_repository_set():
    from scripts.evolution_activation_gate import validate_products
    state={"schema_version":2,"products":{"knowledge-engine":{
        "enabled":True,"repositories":{"jweter/core":"a","jweter/web":"b"}}}}
    declared={"knowledge-engine":["jweter/core","jweter/web","jweter/ai"]}
    result=validate_products(state,{"jweter/core":"a","jweter/web":"b","jweter/ai":"c"},declared)
    assert result["knowledge-engine"]=={"allowed":False,"reason":"PRODUCT_REPOSITORY_SET_MISMATCH"}

def test_contract_rejects_undeclared_product():
    from scripts.evolution_activation_gate import validate_products
    state={"schema_version":2,"products":{"ghost":{
        "enabled":True,"repositories":{"jweter/ghost":"a"}}}}
    result=validate_products(state,{"jweter/ghost":"a"},{"rocksmith":["jweter/rock"]})
    assert result["ghost"]=={"allowed":False,"reason":"PRODUCT_UNDECLARED"}

def test_contract_allows_complete_declared_product():
    state={"schema_version":2,"reason":"READINESS_PASS","products":{"rocksmith":{
        "enabled":True,"repositories":{"jweter/rock":"abc"}}}}
    assert validate_activation(state,{"jweter/rock":"abc"},{"rocksmith":["jweter/rock"]})==(True,"ACTIVATION_CURRENT")
