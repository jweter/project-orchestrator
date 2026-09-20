import json
from scripts.continuous_flow_supervisor import maybe_record_async_rotation, selected_builder_repository

def test_reads_repository_selected_by_real_builder_state(tmp_path):
    (tmp_path/"builder.json").write_text(json.dumps({"status":"PR_OPEN","repository":"jweter/Project-Everward"}))
    assert selected_builder_repository(tmp_path)=="jweter/Project-Everward"

def test_wires_previous_async_gate_to_different_current_selection(tmp_path):
    evidence=maybe_record_async_rotation(tmp_path,
        previous_repository="jweter/knowledge-engine-ai",previous_status="REVIEW_BLOCKED",
        current_repository="jweter/Project-Everward",burst_id="b",process_instance_id="p")
    assert evidence["rotated"] is True
    persisted=json.loads((tmp_path/"async-gate-rotation-history.json").read_text())
    assert persisted["rotations"][-1]["blocked_repository"]=="jweter/knowledge-engine-ai"
    assert persisted["rotations"][-1]["next_repository"]=="jweter/Project-Everward"

def test_non_async_previous_pass_does_not_claim_rotation(tmp_path):
    assert maybe_record_async_rotation(tmp_path,previous_repository="repo-a",previous_status="PR_OPEN",
        current_repository="repo-b",burst_id="b",process_instance_id="p") is None
    assert not (tmp_path/"async-gate-rotation-history.json").exists()
