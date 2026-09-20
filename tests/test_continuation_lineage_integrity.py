import json
import pytest
import scripts.continuous_flow_supervisor as supervisor

def _record(root):
    return supervisor.record_continuation_pass(root,builder_status="PR_OPEN",delay_seconds=1,
        lifecycle_advanced=True,liveness_reacquire={"requested":False},
        burst_id="current-burst",process_instance_id="current-process")

@pytest.mark.parametrize("tail",[
    {"sequence":40,"burst_id":"old","process_instance_id":"p"},
    {"sequence":41,"burst_id":"","process_instance_id":"p"},
    {"sequence":41,"burst_id":"old","process_instance_id":""},
    {"sequence":41,"burst_id":"old"},
    "not-a-record",
])
def test_v2_history_without_authoritative_tail_lineage_resets_sequence(tmp_path,tail):
    history={"schema_version":2,"pass_count":41,"immediate_continuation_count":20,"passes":[tail]}
    (tmp_path/"continuation-pass-history.json").write_text(json.dumps(history))
    record=_record(tmp_path)
    assert record["sequence"]==1
    assert record["burst_id"]=="current-burst"
    assert record["process_instance_id"]=="current-process"

def test_v2_history_with_matching_tail_sequence_preserves_global_sequence(tmp_path):
    history={"schema_version":2,"pass_count":41,"immediate_continuation_count":20,
             "passes":[{"sequence":41,"burst_id":"prior-burst","process_instance_id":"prior-process",
                        "immediate_continuation":False}]}
    (tmp_path/"continuation-pass-history.json").write_text(json.dumps(history))
    record=_record(tmp_path)
    assert record["sequence"]==42
    persisted=json.loads((tmp_path/"continuation-pass-history.json").read_text())
    assert persisted["passes"][-1]["burst_id"]=="current-burst"
    assert persisted["passes"][-1]["process_instance_id"]=="current-process"
