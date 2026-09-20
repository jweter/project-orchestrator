import json
import pytest
from scripts import continuous_flow_supervisor as supervisor

def test_missing_capability_graph_emits_durable_direction_request(tmp_path):
    with pytest.raises(RuntimeError,match="CAPABILITY_GRAPH_REQUIRED"):
        supervisor.load_execution_slice(tmp_path,"owner/repo","abc")
    request=json.loads((tmp_path/"direction-requests"/"owner__repo.json").read_text())
    assert request["status"]=="DIRECTION_REQUIRED"
    assert request["repository"]=="owner/repo"
    assert request["exact_head"]=="abc"
    assert request["issues"][0]["code"]=="CAPABILITY_GRAPH_REQUIRED"

def test_incomplete_slice_emits_specific_direction_issues(tmp_path):
    graphs=tmp_path/"capability-graphs"; graphs.mkdir()
    payload={"schema_version":1,"capabilities":[
        {"id":"cap","state":"PLANNED","depends_on":[],"slices":[]}
    ],"roadmap":["cap"]}
    (graphs/"owner__repo.json").write_text(json.dumps(payload))
    with pytest.raises(RuntimeError):
        supervisor.load_execution_slice(tmp_path,"owner/repo","abc")
    request=json.loads((tmp_path/"direction-requests"/"owner__repo.json").read_text())
    assert request["status"]=="DIRECTION_REQUIRED"
    assert request["issues"]

def test_ready_slice_clears_stale_direction_request(tmp_path,monkeypatch):
    graphs=tmp_path/"capability-graphs"; graphs.mkdir()
    (graphs/"owner__repo.json").write_text("{}")
    requests=tmp_path/"direction-requests"; requests.mkdir()
    stale=requests/"owner__repo.json"; stale.write_text("{}")
    monkeypatch.setattr(supervisor,"derive_slice",lambda *a,**k:{
        "status":"READY","issues":[],"slice":{"capability_id":"c","slice_id":"s"}})
    assert supervisor.load_execution_slice(tmp_path,"owner/repo","abc")["slice_id"]=="s"
    assert not stale.exists()
