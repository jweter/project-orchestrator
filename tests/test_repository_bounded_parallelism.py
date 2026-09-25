import json
import scripts.continuous_flow_supervisor as supervisor
from project_orchestrator.parallel_worker_pool import ParallelWorkerPool
from tests.test_parallel_worker_pool import ManualExecutor

def test_dispatch_never_overprovisions_single_repository_products(tmp_path):
    activation={"products":{
        "knowledge-engine":{"repositories":{"jweter/core":"a","jweter/web":"b","jweter/ai":"c"}},
        "rocksmith":{"repositories":{"jweter/rocksmith":"d"}},
        "everward":{"repositories":{"jweter/everward":"e"}},
    }}
    (tmp_path/"hands-off-activation.json").write_text(json.dumps(activation),encoding="utf-8")
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
