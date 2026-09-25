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
    assert len(snap["leases"])==1
    assert snap["leases"][0]["runtime_id"]=="native-windows"
    assert snap["leases"][0]["worker_id"]=="w1"
    assert snap["leases"][0]["owner_pid"]>0
    assert snap["leases"][0]["owner_fingerprint"]
    assert snap["leases"][0]["lease_id"]

def test_durable_lease_survives_pool_restart_when_owner_identity_matches(tmp_path):
    state=tmp_path/"leases.json"
    fp=lambda pid:f"proc:{pid}:start"
    alive=lambda pid:True
    first=RuntimeLeasePool({"laptop-actor":1},state,fp,alive)
    first.acquire("laptop-actor","w1",owner_pid=77)
    second=RuntimeLeasePool({"laptop-actor":1},state,fp,alive)
    assert second.running("laptop-actor")==1
    with pytest.raises(RuntimeError,match="RUNTIME_SATURATED"):
        second.acquire("laptop-actor","w2",owner_pid=88)

def test_restart_recovers_stale_lease_when_owner_died(tmp_path):
    state=tmp_path/"leases.json"
    fp=lambda pid:f"proc:{pid}:start"
    first=RuntimeLeasePool({"laptop-actor":1},state,fp,lambda pid:True)
    first.acquire("laptop-actor","w1",owner_pid=77)
    second=RuntimeLeasePool({"laptop-actor":1},state,fp,lambda pid:False)
    assert second.running("laptop-actor")==0
    assert second.acquire("laptop-actor","w2",owner_pid=88).worker_id=="w2"

def test_restart_recovers_pid_reuse_fingerprint_mismatch(tmp_path):
    state=tmp_path/"leases.json"
    first=RuntimeLeasePool({"laptop-actor":1},state,lambda pid:"old-start",lambda pid:True)
    first.acquire("laptop-actor","w1",owner_pid=77)
    second=RuntimeLeasePool({"laptop-actor":1},state,lambda pid:"new-start",lambda pid:True)
    assert second.running("laptop-actor")==0
