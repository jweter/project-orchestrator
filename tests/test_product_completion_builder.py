from scripts.product_completion_proof import build_completion_proof

HEAD="a"*40
CRITERIA=[
    {"id":"acceptance","verification_kind":"acceptance"},
    {"id":"reality","verification_kind":"product_reality"},
]

def ev(cid,kind,head=HEAD,status="PASS"):
    return {"criterion_id":cid,"verification_kind":kind,"exact_head":head,
            "status":status,"evidence_ref":f"evidence:{cid}"}

def test_all_declared_criteria_must_be_proven_with_matching_kind_and_head():
    result=build_completion_proof(exact_head=HEAD,criteria=CRITERIA,
        evidence=[ev("acceptance","acceptance"),ev("reality","product_reality")])
    assert result["status"]=="VERIFIED"
    assert [c["name"] for c in result["product_completion"]["checks"]]==["acceptance","reality"]

def test_missing_declared_criterion_fails_closed():
    result=build_completion_proof(exact_head=HEAD,criteria=CRITERIA,
        evidence=[ev("acceptance","acceptance")])
    assert result["status"]=="DIRECTION_REQUIRED"
    assert result["issues"][0]["criterion_id"]=="reality"

def test_wrong_verification_kind_cannot_satisfy_criterion():
    result=build_completion_proof(exact_head=HEAD,criteria=CRITERIA,
        evidence=[ev("acceptance","ci"),ev("reality","product_reality")])
    assert result["status"]=="DIRECTION_REQUIRED"

def test_stale_head_cannot_satisfy_criterion():
    result=build_completion_proof(exact_head=HEAD,criteria=CRITERIA,
        evidence=[ev("acceptance","acceptance","b"*40),ev("reality","product_reality")])
    assert result["status"]=="DIRECTION_REQUIRED"

def test_undeclared_evidence_fails_closed():
    result=build_completion_proof(exact_head=HEAD,criteria=CRITERIA,
        evidence=[ev("acceptance","acceptance"),ev("reality","product_reality"),ev("extra","ci")])
    assert result["status"]=="DIRECTION_REQUIRED"
    assert result["issues"][0]["code"]=="UNDECLARED_COMPLETION_EVIDENCE"
