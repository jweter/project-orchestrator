from scripts.project_capability_graph import validate

def cap(id,state="PLANNED",deps=None,evidence=None,verification=None):
    x={"id":id,"capability":f"Capability {id}","state":state,"depends_on":deps or [],"completion_evidence":evidence or ["passing acceptance test"]}
    if verification is not None:x["verification"]=verification
    return x

def test_selects_only_dependency_ready_remaining_work():
    r=validate({"capabilities":[cap("C1","VERIFIED_COMPLETE",verification={"test":"green"}),cap("C2",deps=["C1"]),cap("C3",deps=["C2"])],"roadmap":["C2","C3"]})
    assert r["status"]=="READY"
    assert r["eligible"]==["C2"]
    assert r["remaining"]==["C2","C3"]

def test_verified_complete_requires_evidence_of_verification():
    r=validate({"capabilities":[cap("C1","VERIFIED_COMPLETE")]})
    assert r["status"]=="DIRECTION_REQUIRED"
    assert any(i["code"]=="VERIFICATION_REQUIRED" for i in r["issues"])

def test_unknown_dependency_fails_closed():
    r=validate({"capabilities":[cap("C1",deps=["C404"])],"roadmap":["C1"]})
    assert r["status"]=="DIRECTION_REQUIRED"
    assert any(i["code"]=="UNKNOWN_DEPENDENCY" for i in r["issues"])

def test_dependency_cycle_fails_closed():
    r=validate({"capabilities":[cap("C1",deps=["C2"]),cap("C2",deps=["C1"])],"roadmap":["C1","C2"]})
    assert r["status"]=="DIRECTION_REQUIRED"
    assert any(i["code"]=="DEPENDENCY_CYCLE" for i in r["issues"])

def test_all_verified_is_complete():
    r=validate({"capabilities":[cap("C1","VERIFIED_COMPLETE",verification={"pr":12})]})
    assert r["status"]=="COMPLETE"
    assert r["remaining"]==[]

def test_roadmap_selects_first_dependency_eligible_capability():
    r=validate({"capabilities":[cap("C1"),cap("C2")],"roadmap":["C2","C1"]})
    assert r["status"]=="READY"
    assert r["selected"]=="C2"

def test_roadmap_cannot_skip_remaining_capability():
    r=validate({"capabilities":[cap("C1"),cap("C2")],"roadmap":["C1"]})
    assert r["status"]=="DIRECTION_REQUIRED"
    assert any(i["code"]=="ROADMAP_OMITS_REMAINING" for i in r["issues"])

def test_empty_roadmap_with_remaining_work_requests_direction():
    r=validate({"capabilities":[cap("C1")],"roadmap":[]})
    assert r["status"]=="DIRECTION_REQUIRED"
    assert any(i["code"]=="ROADMAP_REQUIRED_FOR_REMAINING_WORK" for i in r["issues"])

def test_roadmap_cannot_keep_verified_complete_work():
    r=validate({"capabilities":[cap("C1","VERIFIED_COMPLETE",verification={"test":"green"}),cap("C2")],"roadmap":["C1","C2"]})
    assert r["status"]=="DIRECTION_REQUIRED"
    assert any(i["code"]=="ROADMAP_STALE_COMPLETE" for i in r["issues"])
