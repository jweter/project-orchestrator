import json
import scripts.continuous_flow_supervisor as supervisor
from project_orchestrator.parallel_worker_pool import ParallelWorkerPool, WorkerSlot
from tests.test_parallel_worker_pool import ManualExecutor

def test_parallel_harvest_reads_each_workers_own_state(tmp_path):
    executor=ManualExecutor(max_workers=2,thread_name_prefix="x")
    pool=ParallelWorkerPool(max_workers_per_product=1,max_total_workers=2,
                            executor_factory=lambda **_:executor)
    pool.submit(WorkerSlot("rocksmith",1),lambda:None)
    pool.submit(WorkerSlot("everward",1),lambda:None)
    for worker,status,inv in [
        ("rocksmith-worker-1","PREFLIGHT_GREEN","r1"),
        ("everward-worker-1","NO_WORK",None),
    ]:
        d=tmp_path/"worker-state"/worker; d.mkdir(parents=True)
        (d/"builder.json").write_text(json.dumps({"status":status,"invocation_id":inv}),encoding="utf-8")
    executor.submissions[0][0].set_result((0,"r1"))
    executor.submissions[1][0].set_result((0,None))
    rows=supervisor.harvest_parallel_builders(pool,tmp_path)
    assert rows["rocksmith-worker-1"]["healthy"] is True
    assert rows["rocksmith-worker-1"]["status"]=="PREFLIGHT_GREEN"
    assert rows["everward-worker-1"]["healthy"] is False
    assert rows["everward-worker-1"]["status"]=="NO_WORK"
