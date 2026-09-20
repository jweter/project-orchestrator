from scripts.continuous_flow_supervisor import maybe_record_async_rotation

def test_async_gate_rotates_to_different_repository(tmp_path):
    evidence=maybe_record_async_rotation(tmp_path,previous_repository="jweter/knowledge-engine-ai",
        previous_status="CI_PENDING",current_repository="jweter/Project-Everward",
        burst_id="b",process_instance_id="p")
    assert evidence["rotated"] is True
    assert evidence["blocked_repository"]=="jweter/knowledge-engine-ai"
    assert evidence["next_repository"]=="jweter/Project-Everward"

def test_non_async_state_does_not_manufacture_rotation(tmp_path):
    assert maybe_record_async_rotation(tmp_path,previous_repository="repo-a",previous_status="PR_OPEN",
        current_repository="repo-b",burst_id="b",process_instance_id="p") is None

def test_missing_real_selection_does_not_manufacture_rotation(tmp_path):
    assert maybe_record_async_rotation(tmp_path,previous_repository="repo-a",previous_status="REVIEW_BLOCKED",
        current_repository=None,burst_id="b",process_instance_id="p") is None
