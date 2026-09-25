import json
import scripts.continuous_flow_supervisor as supervisor
from project_orchestrator.parallel_worker_pool import ParallelWorkerPool
from tests.test_parallel_worker_pool import ManualExecutor

def test_dispatch_capacity_is_driven_by_ready_tasks_not_repository_count(tmp_path,monkeypatch):
    activation={"products":{
        "knowledge-engine":{"repositories":{"jweter/core":"a","jweter/web":"b","jweter/ai":"c"}},
        "rocksmith":{"repositories":{"jweter/rocksmith":"d"}},
        "everward":{"repositories":{"jweter/everward":"e"}},
    }}
    (tmp_path/"hands-off-activation.json").write_text(json.dumps(activation),encoding="utf-8")
    def task(repo,head,sid):
        return (repo,head,{"repository":repo,"exact_head":head,"slice_id":sid,"allowed_paths":[sid+"/"]})
    batches={
      "knowledge-engine":[task("jweter/core","a","k1"),task("jweter/core","a","k2")],
      "rocksmith":[task("jweter/rocksmith","d","r1")],
      "everward":[task("jweter/everward","e","e1")],
    }
    monkeypatch.setattr(supervisor,"ready_task_batch",lambda state,act,product,limit:batches[product][:limit])
    monkeypatch.setattr(supervisor,"RUNTIME_LEASES",supervisor.RuntimeLeasePool(supervisor.DEFAULT_RUNTIME_CAPACITIES))
    pool=ParallelWorkerPool(max_workers_per_product=2,max_total_workers=6,executor_factory=ManualExecutor)
    workers=supervisor.dispatch_parallel_builders(
        pool,tmp_path,tmp_path,tmp_path,["knowledge-engine","rocksmith","everward"])
    assert workers==[
        "knowledge-engine-worker-1","rocksmith-worker-1","everward-worker-1",
        "knowledge-engine-worker-2",
    ]
    assert pool.snapshot()["running_by_product"]=={
        "knowledge-engine":2,"rocksmith":1,"everward":1,
    }
