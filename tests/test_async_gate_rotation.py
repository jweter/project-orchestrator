import pytest
from scripts.continuous_flow_supervisor import continuation_delay

@pytest.mark.parametrize("status",["REVIEW_BLOCKED","CI_PENDING","PR_BLOCKED"])
def test_async_gate_rotates_supervisor_immediately(status):
    assert continuation_delay(lifecycle_advanced=False,liveness_reacquire={"requested":False},
        builder_ok=True,builder_status=status,normal_seconds=300)==1

def test_true_no_work_keeps_bounded_backoff():
    assert continuation_delay(lifecycle_advanced=False,liveness_reacquire={"requested":False},
        builder_ok=True,builder_status="NO_WORK",normal_seconds=300)==300
