import json
import pytest
import scripts.continuous_flow_supervisor as supervisor

def _record(root):
    return supervisor.record_continuation_pass(root,builder_status="PR_OPEN",delay_seconds=1,
        lifecycle_advanced=True,liveness_reacquire={"requested":False},
        burst_id="b",process_instance_id="p")

@pytest.mark.parametrize("history",[
    {"schema_version":999,"pass_count":9,"immediate_continuation_count":9,"passes":[]},
    {"schema_version":2,"pass_count":-1,"immediate_continuation_count":0,"passes":[]},
    {"schema_version":2,"pass_count":True,"immediate_continuation_count":0,"passes":[]},
    {"schema_version":2,"pass_count":10000001,"immediate_continuation_count":0,"passes":[]},
    {"schema_version":2,"pass_count":3,"immediate_continuation_count":4,"passes":[]},
    {"schema_version":2,"pass_count":3,"immediate_continuation_count":True,"passes":[]},
    {"schema_version":2,"pass_count":3,"immediate_continuation_count":2,"passes":{}},
])
def test_invalid_history_cannot_manufacture_or_rewind_sequence(tmp_path,history):
    (tmp_path/"continuation-pass-history.json").write_text(json.dumps(history))
    record=_record(tmp_path)
    assert record["sequence"]==1
    persisted=json.loads((tmp_path/"continuation-pass-history.json").read_text())
    assert persisted["schema_version"]==2
    assert persisted["pass_count"]==1
    assert persisted["immediate_continuation_count"]==1

def test_valid_history_advances_monotonically(tmp_path):
    history={"schema_version":2,"pass_count":41,"immediate_continuation_count":20,
             "passes":[{"sequence":41,"immediate_continuation":False}]}
    (tmp_path/"continuation-pass-history.json").write_text(json.dumps(history))
    record=_record(tmp_path)
    assert record["sequence"]==42
    persisted=json.loads((tmp_path/"continuation-pass-history.json").read_text())
    assert persisted["pass_count"]==42
    assert persisted["immediate_continuation_count"]==21

def test_legacy_v1_history_migrates_without_losing_total(tmp_path):
    history={"schema_version":1,"pass_count":5,"immediate_continuation_count":2,"passes":[]}
    (tmp_path/"continuation-pass-history.json").write_text(json.dumps(history))
    assert _record(tmp_path)["sequence"]==6
    persisted=json.loads((tmp_path/"continuation-pass-history.json").read_text())
    assert persisted["schema_version"]==2
    assert persisted["pass_count"]==6
