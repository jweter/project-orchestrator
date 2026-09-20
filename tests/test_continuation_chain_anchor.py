import json
import pytest
import scripts.continuous_flow_supervisor as supervisor

def _record(root):
    return supervisor.record_continuation_pass(root,builder_status="PR_OPEN",delay_seconds=1,
        lifecycle_advanced=True,liveness_reacquire={"requested":False},
        burst_id="b",process_instance_id="p")

@pytest.mark.parametrize("field,value",[
    ("pass_count",99),
    ("immediate_continuation_count",0),
    ("retained_predecessor_hash","forged"),
    ("chain_head","forged"),
])
def test_metadata_rewrite_invalidates_chain_anchor(tmp_path,field,value):
    _record(tmp_path); _record(tmp_path)
    path=tmp_path/"continuation-pass-history.json"
    history=json.loads(path.read_text())
    history[field]=value
    path.write_text(json.dumps(history))
    fresh=_record(tmp_path)
    assert fresh["sequence"]==1
    rewritten=json.loads(path.read_text())
    assert rewritten["pass_count"]==1
    assert rewritten["anchor_hash"]

def test_chain_anchor_is_stable_for_untampered_history(tmp_path):
    _record(tmp_path); second=_record(tmp_path)
    path=tmp_path/"continuation-pass-history.json"
    before=json.loads(path.read_text())
    assert before["anchor_hash"]
    third=_record(tmp_path)
    after=json.loads(path.read_text())
    assert third["sequence"]==3
    assert after["pass_count"]==3
    assert after["anchor_hash"]!=before["anchor_hash"]


def test_pre_anchor_v3_history_migrates_without_sequence_loss(tmp_path):
    first=_record(tmp_path); second=_record(tmp_path)
    path=tmp_path/"continuation-pass-history.json"
    history=json.loads(path.read_text())
    history["schema_version"]=3
    history.pop("anchor_hash",None)
    path.write_text(json.dumps(history))
    third=_record(tmp_path)
    migrated=json.loads(path.read_text())
    assert third["sequence"]==3
    assert migrated["schema_version"]==4
    assert migrated["pass_count"]==3
    assert migrated["anchor_hash"]
