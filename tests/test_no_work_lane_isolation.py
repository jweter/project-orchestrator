import json
import scripts.continuous_flow_supervisor as flow

def completion_graph(head):
    return {"capabilities":[{"id":"c","capability":"done","state":"VERIFIED_COMPLETE",
        "completion_evidence":["tests"],"verification":{"source":"merge"},"depends_on":[]}],"roadmap":[],
        "product_completion":{"verified":True,"exact_head":head,"evidence":["acceptance"],
            "checks":[{"name":"product-reality","status":"PASS"}]}}

def write_graph(root,repo,payload):
    p=root/"capability-graphs"/f"{repo.replace('/','__')}.json"; p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(payload),encoding="utf-8")

def test_no_work_does_not_emit_direction_request(tmp_path):
    write_graph(tmp_path,"o/done",completion_graph("abc"))
    result=flow.load_execution_slice(tmp_path,"o/done","abc")
    assert result["status"]=="NO_WORK"
    assert not (tmp_path/"direction-requests"/"o__done.json").exists()

def test_selector_skips_completed_lane_and_selects_ready_lane(tmp_path,monkeypatch):
    activation={"products":{"p":{"repositories":{"o/done":"a","o/ready":"b"}}}}
    monkeypatch.setattr(flow,"reserved_quarantine_repositories",lambda root:set())
    monkeypatch.setattr(flow,"load_execution_slice",lambda root,repo,head:
        {"status":"NO_WORK"} if repo=="o/done" else {"capability_id":"c","slice_id":"s"})
    repo,head=flow.select_execution_target(tmp_path,activation,["p"])
    assert (repo,head)==("o/ready","b")
