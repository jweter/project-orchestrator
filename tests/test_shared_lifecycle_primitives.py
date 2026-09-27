import json
import threading

import pytest

import project_orchestrator.grant_lifecycle as lifecycle
import project_orchestrator.state_io as state_io
import scripts.autonomous_slice_builder as builder
import scripts.continuous_flow_supervisor as supervisor


def test_builder_uses_shared_lifecycle_primitives_and_supervisor_does_not_reown_them():
    assert builder.mutate_grant_lifecycle is lifecycle.mutate_grant_lifecycle
    assert builder._acquire_lifecycle_lock is lifecycle.acquire_lifecycle_lock
    assert builder._release_lifecycle_lock is lifecycle.release_lifecycle_lock
    assert not hasattr(supervisor, "mutate_grant_lifecycle")
    assert not hasattr(supervisor, "_acquire_lifecycle_lock")
    assert not hasattr(supervisor, "_release_lifecycle_lock")


def test_builder_and_supervisor_share_atomic_json_writer():
    assert builder.atomic_json is state_io.atomic_json
    assert supervisor.atomic is state_io.atomic_json


def test_shared_lifecycle_mutation_preserves_concurrent_records(tmp_path):
    path = tmp_path / "hands-off-grant-lifecycle.json"
    path.write_text(json.dumps({"grants": {
        "a": {"state": "CLAIMED"}, "b": {"state": "CLAIMED"}
    }}), encoding="utf-8")
    barrier = threading.Barrier(2)
    results = []

    def update(grant_id, marker):
        barrier.wait()
        results.append(lifecycle.mutate_grant_lifecycle(
            path, grant_id,
            lambda record: (record.__setitem__("marker", marker) or True),
        ))

    first = threading.Thread(target=update, args=("a", "A"))
    second = threading.Thread(target=update, args=("b", "B"))
    first.start(); second.start(); first.join(); second.join()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert all(results)
    assert saved["grants"]["a"]["marker"] == "A"
    assert saved["grants"]["b"]["marker"] == "B"


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"state": "ISSUED"}, "QUEUED"),
        ({"state": "DEFERRED", "result_status": "RUNNER_CAPACITY_DEFERRED"}, "QUEUED"),
        ({"state": "CLAIMED"}, "CLAIMED"),
        ({"state": "CLAIMED", "execution_stage": "BASE_VERIFIED"}, "PREPARING"),
        ({"state": "CLAIMED", "execution_stage": "BRANCH_CREATED"}, "PREPARING"),
        ({"state": "CLAIMED", "execution_stage": "EXECUTING"}, "IMPLEMENTING"),
        ({"state": "RUNNING"}, "IMPLEMENTING"),
        ({"state": "CLAIMED", "execution_stage": "EXECUTION_FINISHED"}, "VERIFYING"),
        ({"state": "CLAIMED", "execution_stage": "REPAIR_FINISHED"}, "VERIFYING"),
        ({"state": "ABANDONED", "execution_stage": "COMMITTED"}, "ABANDONED"),
        ({"state": "COMPLETED", "execution_stage": "PR_OPEN", "result_status": "PR_OPEN"}, "PR_OPEN"),
        ({"state": "COMPLETED", "result_status": "CI_PENDING"}, "CI"),
        ({"state": "COMPLETED", "result_status": "REVIEW_BLOCKED"}, "REVIEW"),
        ({"state": "COMPLETED", "result_status": "GREEN"}, "PROMOTING"),
        ({"state": "COMPLETED", "result_status": "NO_OP"}, "COMPLETE"),
        ({"state": "COMPLETED", "outcome_quality": "MERGED_VERIFIED"}, "COMPLETE"),
        ({"state": "FAILED", "result_status": "PREFLIGHT_FAILED"}, "FAILED"),
        ({"state": "ABANDONED"}, "ABANDONED"),
        ({"state": "QUARANTINED"}, "BLOCKED"),
        ({"state": "COMPLETED", "result_status": "MYSTERY"}, "UNKNOWN"),
        ({"state": "ALIEN"}, "UNKNOWN"),
    ],
)
def test_canonical_job_projection_maps_legacy_combinations_fail_closed(record, expected):
    projection = lifecycle.canonical_job_projection(record)

    assert projection["job_state"] == expected
    assert projection["lifecycle_state"] == record.get("state")
    assert projection["execution_stage"] == record.get("execution_stage")
    assert projection["result_status"] == record.get("result_status")
    assert projection["outcome_quality"] == record.get("outcome_quality")


def test_canonical_job_projection_rejects_non_record_input():
    with pytest.raises(TypeError, match="grant record"):
        lifecycle.canonical_job_projection(None)
