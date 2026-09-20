import json
import pytest
import scripts.continuous_flow_supervisor as flow

def test_selector_reports_all_complete_distinct_from_blocked(tmp_path,monkeypatch):
    activation={"products":{"p":{"repositories":{"o/a":"a","o/b":"b"}}}}
    monkeypatch.setattr(flow,"reserved_quarantine_repositories",lambda root:set())
    monkeypatch.setattr(flow,"load_execution_slice",lambda root,repo,head:{"status":"NO_WORK"})
    with pytest.raises(RuntimeError,match="ALL_EXECUTION_LANES_COMPLETE"):
        flow.select_execution_target(tmp_path,activation,["p"])

def test_run_builder_treats_all_complete_as_healthy_no_work(tmp_path,monkeypatch):
    monkeypatch.setattr(flow,"write_execution_grant",
        lambda *args,**kwargs: (_ for _ in ()).throw(RuntimeError("ALL_EXECUTION_LANES_COMPLETE")))
    rc,invocation_id=flow.run_builder(tmp_path,tmp_path,tmp_path,["p"])
    assert rc==0
    assert invocation_id is None
    state=json.loads((tmp_path/"builder.json").read_text())
    assert state=={"reason":"ALL_EXECUTION_LANES_COMPLETE","status":"NO_WORK"}
    assert flow.builder_health(tmp_path,rc)==(True,"NO_WORK")

def test_mixed_complete_and_direction_required_is_not_terminal(tmp_path,monkeypatch):
    activation={"products":{"p":{"repositories":{"o/done":"a","o/blocked":"b"}}}}
    monkeypatch.setattr(flow,"reserved_quarantine_repositories",lambda root:set())
    def load(root,repo,head):
        if repo=="o/done": return {"status":"NO_WORK"}
        raise RuntimeError("EXECUTION_SLICE_NOT_READY:o/blocked:DIRECTION_REQUIRED")
    monkeypatch.setattr(flow,"load_execution_slice",load)
    with pytest.raises(RuntimeError,match="NO_READY_EXECUTION_SLICE"):
        flow.select_execution_target(tmp_path,activation,["p"])
