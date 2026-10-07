import pytest

from project_orchestrator.accountability import (
    assert_roadmap_accountability,
    assert_runtime_accountability,
    roadmap_accountability,
    runtime_acceptance,
)


def test_merged_change_stays_open_until_exact_runtime_consumption_and_flow() -> None:
    merged = [{"repository": "jweter/project-orchestrator", "merge_sha": "abc"}]
    runtime = {"checkout_revision": "abc", "actor_revision": "old", "process_instance_id": "old-gen",
               "heartbeat_fresh": True, "state_fresh": True, "dispatch_fresh": True, "productive_flow_proven": True}
    assert runtime_acceptance(merged, runtime)[0]["state"] == "SYNCED_NOT_LOADED"

    runtime.update(actor_revision="abc", process_instance_id="new-gen")
    assert runtime_acceptance(merged, runtime)[0]["state"] == "ACCEPTED"


def test_runtime_accountability_fails_when_unaccepted_merge_is_dropped() -> None:
    merged = [{"repository": "jweter/project-orchestrator", "merge_sha": "abc"}]
    runtime = {"checkout_revision": "old", "actor_revision": "old"}
    with pytest.raises(RuntimeError, match="RUNTIME_ACCOUNTABILITY_DEFECT"):
        assert_runtime_accountability(merged, runtime, [])


def test_unfinished_idle_roadmap_with_capacity_requires_next_slice() -> None:
    snapshot = {"projects": [{"id": "everward", "repositories": [{
        "name": "jweter/Project-Everward", "roadmap_complete": False, "capacity_available": True,
        "started_work": [], "new_roadmap_slice": None,
    }]}]}
    obligation = roadmap_accountability(snapshot)[0]
    assert obligation["state"] == "ROADMAP_PIPELINE_IDLE"
    assert obligation["required_action"] == "create_next_slice"
    with pytest.raises(RuntimeError, match="ROADMAP_ACCOUNTABILITY_DEFECT"):
        assert_roadmap_accountability(snapshot, [])


def test_active_slice_remains_accounted_for_instead_of_creating_duplicate() -> None:
    snapshot = {"projects": [{"id": "rocksmith", "repositories": [{
        "name": "jweter/rocksmith-cdlc-generator", "roadmap_complete": False,
        "capacity_available": True, "started_work": [{"ref": "slice-42"}], "new_roadmap_slice": "slice-43",
    }]}]}
    obligation = roadmap_accountability(snapshot)[0]
    assert (obligation["state"], obligation["required_action"]) == ("SLICE_ACTIVE", "finish_started_work")
