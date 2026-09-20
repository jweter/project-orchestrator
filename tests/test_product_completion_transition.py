from scripts.capability_slice_contract import derive_slice

def graph():
    return {"capabilities":[{"id":"c1","capability":"thing","state":"VERIFIED_COMPLETE",
        "completion_evidence":["tests"],"verification":{"source":"merge"},"depends_on":[]}],"roadmap":[]}

def test_exhausted_capabilities_require_product_completion_evidence():
    result=derive_slice(graph(),repository="o/r",exact_head="abc")
    assert result["status"]=="DIRECTION_REQUIRED"
    assert result["issues"]==[{"code":"PRODUCT_COMPLETION_EVIDENCE_REQUIRED"}]

def test_verified_product_completion_returns_no_work():
    payload=graph()
    payload["product_completion"]={"verified":True,"evidence":["acceptance-suite"]}
    result=derive_slice(payload,repository="o/r",exact_head="abc")
    assert result["status"]=="NO_WORK"
    assert result["slice"] is None

def test_verified_capabilities_do_not_manufacture_more_work():
    payload=graph()
    payload["product_completion"]={"verified":True}
    result=derive_slice(payload,repository="o/r",exact_head="new-head")
    assert result["status"]=="NO_WORK"
    assert result["issues"]==[]
