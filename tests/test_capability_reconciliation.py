import json
from scripts.continuous_flow_supervisor import reconcile_capability_from_slice_evidence

def graph(root, slices, state="IN_PROGRESS"):
    p=root/"capability-graphs"/"o__r.json"; p.parent.mkdir()
    p.write_text(json.dumps({"capabilities":[{"id":"cap","capability":"C","state":state,
        "completion_evidence":["all slices"],"depends_on":[],"slices":slices}]}))
    return p

def evidence(root,sid,head="abc",pr=7):
    p=root/"slice-completion-evidence"/f"o__r__{sid}.json"; p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps({"status":"VERIFIED_COMPLETE","repository":"o/r","capability_id":"cap",
        "slice_id":sid,"head_sha":head,"pr_number":pr,"grant_id":"g"}))

def slice_(sid):
    return {"id":sid,"state":"IMPLEMENTED_UNVERIFIED","objective":"x","acceptance":["pass"]}

def test_one_of_multiple_verified_slices_keeps_capability_in_progress(tmp_path):
    p=graph(tmp_path,[slice_("s1"),slice_("s2")]); evidence(tmp_path,"s1")
    r=reconcile_capability_from_slice_evidence(tmp_path,"o/r","cap")
    assert r["status"]=="CAPABILITY_IN_PROGRESS"
    cap=json.loads(p.read_text())["capabilities"][0]
    assert cap["slices"][0]["state"]=="VERIFIED_COMPLETE"
    assert cap["slices"][1]["state"]=="IMPLEMENTED_UNVERIFIED"
    assert cap["state"]=="IN_PROGRESS"

def test_all_required_slices_complete_capability(tmp_path):
    p=graph(tmp_path,[slice_("s1"),slice_("s2")]); evidence(tmp_path,"s1",pr=7); evidence(tmp_path,"s2",head="def",pr=8)
    r=reconcile_capability_from_slice_evidence(tmp_path,"o/r","cap")
    assert r["status"]=="CAPABILITY_VERIFIED_COMPLETE"
    cap=json.loads(p.read_text())["capabilities"][0]
    assert cap["state"]=="VERIFIED_COMPLETE"
    assert cap["verification"]["source"]=="all_required_slices_verified"
    assert cap["verification"]["slice_ids"]==["s1","s2"]

def test_conflicting_evidence_fails_closed(tmp_path):
    p=graph(tmp_path,[slice_("s1")]); evidence(tmp_path,"s1")
    e=tmp_path/"slice-completion-evidence"/"o__r__s1.json"
    data=json.loads(e.read_text()); data["capability_id"]="other"; e.write_text(json.dumps(data))
    r=reconcile_capability_from_slice_evidence(tmp_path,"o/r","cap")
    assert r["status"]=="SLICE_EVIDENCE_IDENTITY_CONFLICT"
    assert json.loads(p.read_text())["capabilities"][0]["state"]=="IN_PROGRESS"
