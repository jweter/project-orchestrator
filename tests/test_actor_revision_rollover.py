import json
from pathlib import Path

import project_orchestrator.supervisor_runtime as runtime


class Pool:
    def __init__(self, running: int):
        self.running = running

    def snapshot(self):
        return {"running_total": self.running}


class Child:
    pid = 12345

    def terminate(self):
        raise AssertionError("proved successor must not be terminated")


def test_revision_rollover_launches_successor_before_takeover_request(tmp_path: Path):
    repo = tmp_path / "repo"
    workspace = tmp_path / "work"
    state = tmp_path / "state"
    (repo / "scripts").mkdir(parents=True)
    workspace.mkdir()
    state.mkdir()
    calls = []

    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))
        assert not (state / "supervisor-takeover-request.json").exists()
        return Child()

    result = runtime.prepare_actor_revision_rollover(
        repo, workspace, state,
        loaded_actor_revision="old", current_actor_revision="new",
        process_instance_id="generation-a", worker_pool=Pool(2),
        builder_seconds=300, merge_seconds=60,
        popen_fn=fake_popen, fingerprint_fn=lambda pid: f"fp:{pid}",
    )

    assert result == {"requested": True, "status": "WAITING_FOR_WORKERS", "active_workers": 2}
    assert calls
    successor = json.loads((state / "supervisor-revision-successor.json").read_text())
    request = json.loads((state / "supervisor-takeover-request.json").read_text())
    assert successor["predecessor_process_instance_id"] == "generation-a"
    assert successor["process_start_fingerprint"] == "fp:12345"
    assert request["predecessor_process_instance_id"] == "generation-a"
    assert request["reason"] == "ACTOR_REVISION_ADVANCED"


def test_revision_rollover_reuses_live_successor_launcher(tmp_path: Path):
    repo = tmp_path / "repo"
    workspace = tmp_path / "work"
    state = tmp_path / "state"
    (repo / "scripts").mkdir(parents=True)
    workspace.mkdir()
    state.mkdir()

    runtime.prepare_actor_revision_rollover(
        repo, workspace, state,
        loaded_actor_revision="old", current_actor_revision="new",
        process_instance_id="generation-a", worker_pool=Pool(1),
        builder_seconds=300, merge_seconds=60,
        popen_fn=lambda *a, **k: Child(), fingerprint_fn=lambda pid: f"fp:{pid}",
    )

    def should_not_launch(*_args, **_kwargs):
        raise AssertionError("live successor launcher must be reused")

    result = runtime.prepare_actor_revision_rollover(
        repo, workspace, state,
        loaded_actor_revision="old", current_actor_revision="new",
        process_instance_id="generation-a", worker_pool=Pool(0),
        builder_seconds=300, merge_seconds=60,
        popen_fn=should_not_launch, fingerprint_fn=lambda pid: f"fp:{pid}",
    )
    assert result["requested"] is True
    assert result["status"] == "DRAINING_FOR_TAKEOVER"


def test_revision_drain_disables_new_dispatch_and_recovery():
    source = Path("scripts/continuous_flow_supervisor.py").read_text(encoding="utf-8")
    assert 'if products and not revision_rollover["requested"] else []' in source
    assert 'if not revision_rollover["requested"] and not dispatched_workers' in source
    assert 'if products and not revision_rollover["requested"] and parallel_results' in source
