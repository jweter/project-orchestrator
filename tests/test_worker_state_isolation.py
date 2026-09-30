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
    for worker,status,inv,repository in [
        ("rocksmith-worker-1","PREFLIGHT_GREEN","r1","jweter/rocksmith-cdlc-generator"),
        ("everward-worker-1","NO_WORK",None,"jweter/Project-Everward"),
    ]:
        d=tmp_path/"worker-state"/worker; d.mkdir(parents=True)
        (d/"builder.json").write_text(json.dumps({
            "status":status,"invocation_id":inv,"repository":repository,
        }),encoding="utf-8")
    executor.submissions[0][0].set_result((0,"r1"))
    executor.submissions[1][0].set_result((0,None))
    rows=supervisor.harvest_parallel_builders(pool,tmp_path)
    assert rows["rocksmith-worker-1"]["healthy"] is True
    assert rows["rocksmith-worker-1"]["status"]=="PREFLIGHT_GREEN"
    assert rows["rocksmith-worker-1"]["repository"]=="jweter/rocksmith-cdlc-generator"
    assert rows["everward-worker-1"]["healthy"] is False
    assert rows["everward-worker-1"]["status"]=="NO_WORK"


def test_parallel_harvest_rejects_repository_from_stale_invocation(tmp_path):
    executor=ManualExecutor(max_workers=1,thread_name_prefix="x")
    pool=ParallelWorkerPool(max_workers_per_product=1,max_total_workers=1,
                            executor_factory=lambda **_:executor)
    pool.submit(WorkerSlot("rocksmith",1),lambda:None)
    d=tmp_path/"worker-state"/"rocksmith-worker-1"; d.mkdir(parents=True)
    (d/"builder.json").write_text(json.dumps({
        "status":"PREFLIGHT_GREEN",
        "invocation_id":"old-invocation",
        "repository":"jweter/rocksmith-cdlc-generator",
    }),encoding="utf-8")
    executor.submissions[0][0].set_result((0,"current-invocation"))
    rows=supervisor.harvest_parallel_builders(pool,tmp_path)
    assert rows["rocksmith-worker-1"]["status"]=="STALE_BUILDER_STATE"
    assert rows["rocksmith-worker-1"]["repository"] is None


def test_supervisor_uses_harvested_repository_for_throughput_proof():
    import inspect
    source=inspect.getsource(supervisor.main)
    assert 'current_repository=repository' in source
    assert 'record_burst_repository_pass(state,repository=current_repository' in source
    assert 'record_product_identity_advancement(state,repository=current_repository' in source


def test_runtime_lease_reconciliation_uses_post_harvest_active_workers():
    from project_orchestrator.supervisor_runtime import reconcile_runtime_leases

    class Pool:
        def snapshot(self):
            return {"workers":[{"worker_id":"rocksmith-worker-1"}]}

    class Leases:
        def __init__(self):
            self.active=None
        def recover_stale(self):
            return ["dead-old"]
        def reconcile_owned_workers(self,active):
            self.active=active
            return ["knowledge-engine-worker-1"]

    leases=Leases()
    assert reconcile_runtime_leases(leases,Pool())==[
        "dead-old","knowledge-engine-worker-1"
    ]
    assert leases.active=={"rocksmith-worker-1"}


def test_supervisor_reconciles_current_generation_runtime_leases_after_harvest():
    import inspect
    source=inspect.getsource(supervisor.main)
    harvest=source.index("parallel_results=harvest_parallel_builders(worker_pool,state)")
    reconcile=source.index("_supervisor_runtime.reconcile_runtime_leases(",harvest)
    dispatch=source.index("dispatch_parallel_builders(",reconcile)
    assert harvest < reconcile < dispatch
