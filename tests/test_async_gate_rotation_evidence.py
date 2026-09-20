import json
from scripts.continuous_flow_supervisor import record_async_gate_rotation

def test_records_cross_repository_rotation_as_productive_evidence(tmp_path):
    record=record_async_gate_rotation(tmp_path,blocked_repository="jweter/knowledge-engine-ai",
        blocked_status="REVIEW_BLOCKED",next_repository="jweter/Project-Everward",
        burst_id="burst-1",process_instance_id="proc-1")
    assert record["rotated"] is True
    persisted=json.loads((tmp_path/"async-gate-rotation-history.json").read_text())
    assert persisted["rotation_count"]==1
    assert persisted["rotations"][-1]["next_repository"]=="jweter/Project-Everward"

def test_same_repository_is_not_false_rotation_proof(tmp_path):
    record=record_async_gate_rotation(tmp_path,blocked_repository="jweter/knowledge-engine-ai",
        blocked_status="CI_PENDING",next_repository="jweter/knowledge-engine-ai",
        burst_id="burst-1",process_instance_id="proc-1")
    assert record["rotated"] is False

def test_rotation_history_is_bounded_but_count_remains_monotonic(tmp_path):
    for i in range(105):
        record_async_gate_rotation(tmp_path,blocked_repository=f"repo-{i}",
            blocked_status="PR_BLOCKED",next_repository=f"repo-{i+1}",
            burst_id="b",process_instance_id="p")
    persisted=json.loads((tmp_path/"async-gate-rotation-history.json").read_text())
    assert persisted["rotation_count"]==105
    assert len(persisted["rotations"])==100
