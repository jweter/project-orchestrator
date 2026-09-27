import json
import threading

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
