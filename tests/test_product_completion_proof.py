from scripts.capability_slice_contract import derive_slice

def completed():
    return {"capabilities":[{"id":"c","capability":"done","state":"VERIFIED_COMPLETE",
        "completion_evidence":["acceptance"],"verification":{"source":"merge"},"depends_on":[]}],"roadmap":[]}

def proof(head="abc"):
    return {"verified":True,"exact_head":head,"evidence":["acceptance-suite"],
            "checks":[{"name":"product-reality","status":"PASS"}]}

def test_completion_proof_must_match_current_exact_head():
    payload=completed(); payload["product_completion"]=proof("old")
    result=derive_slice(payload,repository="o/r",exact_head="new")
    assert result["status"]=="DIRECTION_REQUIRED"

def test_completion_requires_concrete_evidence():
    payload=completed(); payload["product_completion"]={"verified":True,"exact_head":"abc","evidence":[],
        "checks":[{"name":"product-reality","status":"PASS"}]}
    assert derive_slice(payload,repository="o/r",exact_head="abc")["status"]=="DIRECTION_REQUIRED"

def test_completion_requires_all_declared_checks_to_pass():
    payload=completed(); payload["product_completion"]=proof()
    payload["product_completion"]["checks"].append({"name":"acceptance","status":"FAIL"})
    assert derive_slice(payload,repository="o/r",exact_head="abc")["status"]=="DIRECTION_REQUIRED"

def test_exact_head_passing_completion_proof_returns_no_work():
    payload=completed(); payload["product_completion"]=proof()
    result=derive_slice(payload,repository="o/r",exact_head="abc")
    assert result["status"]=="NO_WORK"
