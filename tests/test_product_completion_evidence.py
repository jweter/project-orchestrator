from scripts.product_completion_proof import build_completion_proof
HEAD="a"*40

def acceptance(head=HEAD):
    return [{"name":"final-acceptance","status":"PASS","exact_head":head,"evidence_ref":"artifact://acceptance"}]

def reality(head=HEAD,state="VERIFIED"):
    return [{"kind":"packaged_app","state":state,"exact_head":head,"source_ref":"artifact://reality"}]

def test_builds_completion_only_from_exact_head_passing_evidence():
    result=build_completion_proof(exact_head=HEAD,acceptance=acceptance(),product_reality=reality())
    assert result["status"]=="VERIFIED"
    proof=result["product_completion"]
    assert proof["verified"] is True and proof["exact_head"]==HEAD
    assert set(proof["evidence"])=={"artifact://acceptance","artifact://reality"}

def test_stale_acceptance_fails_closed():
    assert build_completion_proof(exact_head=HEAD,acceptance=acceptance("b"*40),product_reality=reality())["status"]=="DIRECTION_REQUIRED"

def test_unverified_product_reality_fails_closed():
    assert build_completion_proof(exact_head=HEAD,acceptance=acceptance(),product_reality=reality(state="PENDING"))["status"]=="DIRECTION_REQUIRED"

def test_no_product_reality_debt_is_valid_when_acceptance_is_proven():
    result=build_completion_proof(exact_head=HEAD,acceptance=acceptance(),product_reality=[])
    assert result["status"]=="VERIFIED"
    assert result["product_completion"]["checks"]==[{"name":"final-acceptance","status":"PASS"}]
