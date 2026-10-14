import json

import pytest
import scripts.continuous_flow_supervisor as supervisor
from project_orchestrator.parallel_worker_pool import ParallelWorkerPool
from tests.test_parallel_worker_pool import ManualExecutor

def test_dispatch_capacity_is_driven_by_ready_tasks_not_repository_count(tmp_path,monkeypatch):
    activation={"products":{
        "knowledge-engine":{"repositories":{"jweter/core":"a","jweter/web":"b","jweter/ai":"c"}},
        "rocksmith":{"repositories":{"jweter/rocksmith":"d"}},
        "everward":{"repositories":{"jweter/everward":"e"}},
        "voicestudio":{"repositories":{"jweter/VoiceStudio":"f"}},
    }}
    (tmp_path/"hands-off-activation.json").write_text(json.dumps(activation),encoding="utf-8")
    def task(repo,head,sid):
        return (repo,head,{"repository":repo,"exact_head":head,"slice_id":sid,"allowed_paths":[sid+"/"]})
    batches={
      "knowledge-engine":[task("jweter/core","a","k1"),task("jweter/core","a","k2")],
      "rocksmith":[task("jweter/rocksmith","d","r1")],
      "everward":[task("jweter/everward","e","e1")],
      "voicestudio":[task("jweter/VoiceStudio","f","v1")],
    }
    monkeypatch.setattr(supervisor,"ready_task_batch",lambda state,act,product,limit:batches[product][:limit])
    monkeypatch.setattr(supervisor,"RUNTIME_LEASES",supervisor.RuntimeLeasePool(supervisor.DEFAULT_RUNTIME_CAPACITIES))
    pool=ParallelWorkerPool(max_workers_per_product=2,max_total_workers=6,executor_factory=ManualExecutor)
    from project_orchestrator.runner_registry import RunnerManifest,RunnerRegistry
    runners=RunnerRegistry([
        RunnerManifest("aider-ollama",frozenset({"code","local","windows","offline"}),1,30),
        RunnerManifest("claude-code",frozenset({"code","review","local","windows","offline"}),3,20),
    ])
    workers=supervisor.dispatch_parallel_builders(
        pool,tmp_path,tmp_path,tmp_path,["knowledge-engine","rocksmith","everward","voicestudio"],runners)
    # Four independent product lanes consume the available runner slots
    # before any product receives a second intra-product worker.
    assert workers==[
        "knowledge-engine-worker-1","rocksmith-worker-1","everward-worker-1",
        "voicestudio-worker-1",
    ]
    assert pool.snapshot()["running_by_product"]=={
        "knowledge-engine":1,"rocksmith":1,"everward":1,"voicestudio":1,"iron-god":0,
    }


def test_three_aider_workers_can_fill_independent_product_lanes(tmp_path,monkeypatch):
    activation={"products":{
        "knowledge-engine":{"repositories":{"jweter/core":"a"}},
        "rocksmith":{"repositories":{"jweter/rocksmith":"b"}},
        "voicestudio":{"repositories":{"jweter/VoiceStudio":"c"}},
    }}
    (tmp_path/"hands-off-activation.json").write_text(json.dumps(activation),encoding="utf-8")
    def task(repo,head,sid):
        return (repo,head,{"repository":repo,"exact_head":head,"slice_id":sid,"allowed_paths":[sid+"/"]})
    batches={
        "knowledge-engine":[task("jweter/core","a","k1")],
        "rocksmith":[task("jweter/rocksmith","b","r1")],
        "voicestudio":[task("jweter/VoiceStudio","c","v1")],
    }
    monkeypatch.setattr(supervisor,"ready_task_batch",lambda state,act,product,limit:batches[product][:limit])
    monkeypatch.setattr(supervisor,"RUNTIME_LEASES",supervisor.RuntimeLeasePool(supervisor.DEFAULT_RUNTIME_CAPACITIES))
    from project_orchestrator.runner_registry import RunnerManifest,RunnerRegistry
    runners=RunnerRegistry([
        RunnerManifest("aider-ollama",frozenset({"code","local","windows","offline"}),3,30),
    ])
    pool=ParallelWorkerPool(max_workers_per_product=1,max_total_workers=3,executor_factory=ManualExecutor)

    workers=supervisor.dispatch_parallel_builders(
        pool,tmp_path,tmp_path,tmp_path,["knowledge-engine","rocksmith","voicestudio"],runners)

    assert workers==["knowledge-engine-worker-1","rocksmith-worker-1","voicestudio-worker-1"]
    blocks=json.loads((tmp_path/"dispatch-blocks.json").read_text(encoding="utf-8"))["blocked"]
    assert not any(row["reason"]=="NO_ELIGIBLE_RUNNER" for row in blocks)


def test_dispatch_refreshes_runner_health_when_cached_capacity_is_exhausted(tmp_path,monkeypatch):
    activation={"products":{
        "voicestudio":{"repositories":{"jweter/VoiceStudio":"f"}},
    }}
    (tmp_path/"hands-off-activation.json").write_text(json.dumps(activation),encoding="utf-8")
    (tmp_path/"hands-off-grant-lifecycle.json").write_text(json.dumps({"grants":{
        "active-aider":{"state":"ISSUED","runner_id":"aider-ollama"},
    }}),encoding="utf-8")
    task=("jweter/VoiceStudio","f",{
        "repository":"jweter/VoiceStudio",
        "exact_head":"f",
        "slice_id":"v1",
        "allowed_paths":["backend/"],
    })
    monkeypatch.setattr(supervisor,"ready_task_batch",lambda state,act,product,limit:[task])
    monkeypatch.setattr(supervisor,"RUNTIME_LEASES",supervisor.RuntimeLeasePool(supervisor.DEFAULT_RUNTIME_CAPACITIES))
    from project_orchestrator.runner_registry import RunnerManifest,RunnerRegistry
    cached=RunnerRegistry([
        RunnerManifest("aider-ollama",frozenset({"code","local","windows","offline"}),1,30,True),
        RunnerManifest("claude-code",frozenset({"code","review","local","windows","offline"}),3,20,False),
    ])
    fresh=RunnerRegistry([
        RunnerManifest("aider-ollama",frozenset({"code","local","windows","offline"}),1,30,True),
        RunnerManifest("claude-code",frozenset({"code","review","local","windows","offline"}),3,20,True),
    ])
    monkeypatch.setattr(supervisor._parallel_runtime,"DEFAULT_RUNNERS",cached)
    refreshes=[]
    monkeypatch.setattr(supervisor._parallel_runtime,"default_runner_registry",lambda:(refreshes.append(True) or fresh))
    pool=ParallelWorkerPool(max_workers_per_product=1,max_total_workers=1,executor_factory=ManualExecutor)

    workers=supervisor.dispatch_parallel_builders(
        pool,tmp_path,tmp_path,tmp_path,["voicestudio"])

    assert workers==["voicestudio-worker-1"]
    assert refreshes==[True]
    blocked=json.loads((tmp_path/"dispatch-blocks.json").read_text(encoding="utf-8"))["blocked"]
    assert not any(row["reason"]=="NO_ELIGIBLE_RUNNER" for row in blocked)


def test_dispatch_does_not_probe_runner_health_when_cached_runner_is_available(tmp_path,monkeypatch):
    activation={"products":{
        "voicestudio":{"repositories":{"jweter/VoiceStudio":"f"}},
    }}
    (tmp_path/"hands-off-activation.json").write_text(json.dumps(activation),encoding="utf-8")
    task=("jweter/VoiceStudio","f",{
        "repository":"jweter/VoiceStudio","exact_head":"f","slice_id":"v1","allowed_paths":["backend/"],
    })
    monkeypatch.setattr(supervisor,"ready_task_batch",lambda state,act,product,limit:[task])
    monkeypatch.setattr(supervisor,"RUNTIME_LEASES",supervisor.RuntimeLeasePool(supervisor.DEFAULT_RUNTIME_CAPACITIES))
    from project_orchestrator.runner_registry import RunnerManifest,RunnerRegistry
    cached=RunnerRegistry([
        RunnerManifest("aider-ollama",frozenset({"code","local","windows","offline"}),1,30,True),
    ])
    monkeypatch.setattr(supervisor._parallel_runtime,"DEFAULT_RUNNERS",cached)
    monkeypatch.setattr(
        supervisor._parallel_runtime,
        "default_runner_registry",
        lambda:(_ for _ in ()).throw(AssertionError("health refresh should stay off fast path")),
    )
    pool=ParallelWorkerPool(max_workers_per_product=1,max_total_workers=1,executor_factory=ManualExecutor)

    workers=supervisor.dispatch_parallel_builders(
        pool,tmp_path,tmp_path,tmp_path,["voicestudio"])

    assert workers==["voicestudio-worker-1"]


@pytest.mark.parametrize("failure", ["lease", "submit"])
def test_failed_worker_start_does_not_consume_runner_capacity(tmp_path, monkeypatch, failure):
    """A failed first slot must not starve another eligible product lane."""
    from project_orchestrator.runner_registry import RunnerManifest, RunnerRegistry

    activation = {"products": {
        "rocksmith": {"repositories": {"jweter/rocksmith": "a"}},
        "everward": {"repositories": {"jweter/everward": "b"}},
    }}
    (tmp_path / "hands-off-activation.json").write_text(json.dumps(activation), encoding="utf-8")

    def ready(_state, _activation, product, _limit):
        return [(f"jweter/{product}", product, {
            "repository": f"jweter/{product}",
            "exact_head": product,
            "slice_id": f"{product}-slice",
            "allowed_paths": [f"{product}/"],
        })]

    monkeypatch.setattr(supervisor, "ready_task_batch", ready)
    leases = supervisor.RuntimeLeasePool(supervisor.DEFAULT_RUNTIME_CAPACITIES)
    monkeypatch.setattr(supervisor, "RUNTIME_LEASES", leases)
    pool = ParallelWorkerPool(max_workers_per_product=1, max_total_workers=2,
                              executor_factory=ManualExecutor)
    runners = RunnerRegistry([
        RunnerManifest("aider-ollama", frozenset({"code", "local", "windows", "offline"}), 1, 30),
    ])
    if failure == "lease":
        original_acquire = leases.acquire
        calls = []

        def fail_first_lease(*args, **kwargs):
            calls.append(True)
            if len(calls) == 1:
                raise RuntimeError("RUNTIME_LEASE_UNAVAILABLE")
            return original_acquire(*args, **kwargs)

        monkeypatch.setattr(leases, "acquire", fail_first_lease)
    else:
        original_submit = pool.submit
        calls = []

        def fail_first_submit(*args, **kwargs):
            calls.append(True)
            if len(calls) == 1:
                raise RuntimeError("WORKER_SUBMISSION_REJECTED")
            return original_submit(*args, **kwargs)

        monkeypatch.setattr(pool, "submit", fail_first_submit)

    workers = supervisor.dispatch_parallel_builders(
        pool, tmp_path, tmp_path, tmp_path, ["rocksmith", "everward"], runners
    )
    assert workers == ["everward-worker-1"]
    assert pool.snapshot()["running_total"] == 1
    blocks = json.loads((tmp_path / "dispatch-blocks.json").read_text(encoding="utf-8"))["blocked"]
    assert any(row["product"] == "rocksmith" and row["reason"] == (
        "RUNTIME_LEASE_UNAVAILABLE" if failure == "lease" else "WORKER_SUBMISSION_REJECTED"
    ) for row in blocks)
    assert not any(row["reason"] == "NO_ELIGIBLE_RUNNER" for row in blocks)
