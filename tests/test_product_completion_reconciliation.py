import json
import scripts.continuous_flow_supervisor as flow
HEAD="a"*40
REPO="o/r"

def graph():
    return {"capabilities":[{"id":"c","capability":"done","state":"VERIFIED_COMPLETE",
        "completion_evidence":["tests"],"verification":{"source":"merge"},"depends_on":[]}],"roadmap":[]}

def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(value),encoding="utf-8")

def test_load_execution_slice_reconciles_final_evidence_to_no_work(tmp_path):
    graph_path=tmp_path/"capability-graphs"/"o__r.json"; write(graph_path,graph())
    root=tmp_path/"product-completion-evidence"
    write(root/"o__r__acceptance.json",[{"name":"final","status":"PASS","exact_head":HEAD,"evidence_ref":"artifact://accept"}])
    write(root/"o__r__product-reality.json",[{"kind":"packaged_app","state":"VERIFIED","exact_head":HEAD,"source_ref":"artifact://reality"}])
    lifecycle=tmp_path/"hands-off-grant-lifecycle.json"
    write(lifecycle,{"grants":{"g1":{"repository":REPO,"head_sha":HEAD}}})
    result=flow.load_execution_slice(tmp_path,REPO,HEAD)
    assert result["status"]=="NO_WORK"
    grant=json.loads(lifecycle.read_text())["grants"]["g1"]
    assert grant["outcome_quality"]=="PRODUCT_REALITY_VERIFIED"
    persisted=json.loads(graph_path.read_text())
    assert persisted["product_completion"]["exact_head"]==HEAD
    assert persisted["product_completion"]["verified"] is True
    assert not (tmp_path/"direction-requests"/"o__r.json").exists()

def test_stale_final_evidence_still_requests_direction(tmp_path):
    write(tmp_path/"capability-graphs"/"o__r.json",graph())
    root=tmp_path/"product-completion-evidence"
    write(root/"o__r__acceptance.json",[{"name":"final","status":"PASS","exact_head":"b"*40,"evidence_ref":"artifact://old"}])
    try: flow.load_execution_slice(tmp_path,REPO,HEAD)
    except RuntimeError: pass
    request=json.loads((tmp_path/"direction-requests"/"o__r.json").read_text())
    assert request["status"]=="DIRECTION_REQUIRED"
    assert request["reason"]=="DIRECTION_REQUIRED"
