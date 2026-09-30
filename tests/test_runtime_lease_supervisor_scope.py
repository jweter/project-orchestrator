from project_orchestrator.parallel_runtime import scoped_runtime_worker_id
from project_orchestrator.runtime_leases import RuntimeLeasePool
from project_orchestrator.supervisor_runtime import reconcile_runtime_leases

def test_runtime_worker_ids_are_scoped_by_supervisor_state(tmp_path):
    a=scoped_runtime_worker_id(tmp_path/"a","knowledge-engine-worker-1")
    b=scoped_runtime_worker_id(tmp_path/"b","knowledge-engine-worker-1")
    assert a!=b
    assert a.endswith(":knowledge-engine-worker-1")
    assert b.endswith(":knowledge-engine-worker-1")

def test_two_supervisors_can_lease_same_local_slot_name(tmp_path):
    state=tmp_path/"leases.json"
    pool=RuntimeLeasePool({"native-windows":6},state,lambda pid:f"p:{pid}",lambda pid:True)
    a=scoped_runtime_worker_id(tmp_path/"a","knowledge-engine-worker-1")
    b=scoped_runtime_worker_id(tmp_path/"b","knowledge-engine-worker-1")
    pool.acquire("native-windows",a,owner_pid=11)
    pool.acquire("native-windows",b,owner_pid=22)
    assert pool.running("native-windows")==2

def test_reconciliation_preserves_scoped_runtime_and_resource_leases(tmp_path):
    state=tmp_path/"leases.json"
    pool=RuntimeLeasePool({"native-windows":2,"resource:ollama":1},state)
    worker="knowledge-engine-worker-1"
    scoped=scoped_runtime_worker_id(tmp_path/"flow-state",worker)
    pool.acquire("native-windows",scoped)
    pool.acquire("resource:ollama",f"{scoped}:resource:ollama")
    class Pool:
        def snapshot(self):
            return {"workers":[{"worker_id":worker}]}
    assert reconcile_runtime_leases(pool,Pool())==[]
    assert pool.running("native-windows")==1
    assert pool.running("resource:ollama")==1
