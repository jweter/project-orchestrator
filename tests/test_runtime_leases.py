import pytest
from project_orchestrator.runtime_leases import RuntimeLeasePool

def test_runtime_lease_enforces_capacity_and_releases():
    pool=RuntimeLeasePool({"laptop-actor":1})
    lease=pool.acquire("laptop-actor","w1")
    assert lease.runtime_id=="laptop-actor"
    with pytest.raises(RuntimeError,match="RUNTIME_SATURATED"):
        pool.acquire("laptop-actor","w2")
    assert pool.release("w1")
    assert pool.acquire("laptop-actor","w2").worker_id=="w2"

def test_runtime_lease_rejects_unknown_runtime():
    pool=RuntimeLeasePool({"native-windows":2})
    with pytest.raises(RuntimeError,match="RUNTIME_CAPACITY_UNKNOWN"):
        pool.acquire("missing","w")

def test_runtime_lease_rejects_duplicate_worker():
    pool=RuntimeLeasePool({"native-windows":2})
    pool.acquire("native-windows","w")
    with pytest.raises(RuntimeError,match="RUNTIME_LEASE_ALREADY_HELD"):
        pool.acquire("native-windows","w")

def test_runtime_lease_snapshot_is_inspectable():
    pool=RuntimeLeasePool({"native-windows":2,"laptop-actor":1})
    pool.acquire("native-windows","w1")
    snap=pool.snapshot()
    assert snap["running"]=={"native-windows":1,"laptop-actor":0}
    assert snap["leases"]==[{"runtime_id":"native-windows","worker_id":"w1"}]
