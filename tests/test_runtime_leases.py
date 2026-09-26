import os
import subprocess
import sys
import pytest
from project_orchestrator.runtime_leases import RuntimeLeasePool

@pytest.mark.skipif(os.name!="nt",reason="Windows process liveness regression")
def test_windows_pid_liveness_detects_other_live_process():
    child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"])
    try:
        assert child.pid!=os.getpid()
        assert RuntimeLeasePool._default_pid_alive(child.pid)
        assert RuntimeLeasePool._default_fingerprint(child.pid)
    finally:
        child.terminate()
        child.wait(timeout=10)
    assert not RuntimeLeasePool._default_pid_alive(child.pid)


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

def test_two_pools_cannot_double_allocate_same_durable_capacity(tmp_path):
    state=tmp_path/"leases.json"
    fp=lambda pid:f"proc:{pid}:start"
    alive=lambda pid:True
    a=RuntimeLeasePool({"laptop-actor":1},state,fp,alive)
    b=RuntimeLeasePool({"laptop-actor":1},state,fp,alive)
    a.acquire("laptop-actor","w1",owner_pid=77)
    with pytest.raises(RuntimeError,match="RUNTIME_SATURATED"):
        b.acquire("laptop-actor","w2",owner_pid=88)

def test_release_reloads_authoritative_state_before_write(tmp_path):
    state=tmp_path/"leases.json"
    fp=lambda pid:f"proc:{pid}:start"
    alive=lambda pid:True
    a=RuntimeLeasePool({"native-windows":2},state,fp,alive)
    b=RuntimeLeasePool({"native-windows":2},state,fp,alive)
    a.acquire("native-windows","w1",owner_pid=77)
    b.acquire("native-windows","w2",owner_pid=88)
    assert a.release("w1")
    final=RuntimeLeasePool({"native-windows":2},state,fp,alive)
    assert final.running("native-windows")==1
    assert final.snapshot()["leases"][0]["worker_id"]=="w2"

def test_stale_lock_from_dead_owner_is_reclaimed(tmp_path,monkeypatch):
    state=tmp_path/"leases.json"; lock=tmp_path/"leases.json.lock"
    lock.write_text('{"schema_version":1,"pid":77,"fingerprint":"old","token":"dead","acquired_at":1}\n')
    pool=RuntimeLeasePool({"native-windows":1},state,
        fingerprint=lambda pid:"self" if pid==os.getpid() else "old",
        pid_alive=lambda pid:pid==os.getpid())
    assert pool.acquire("native-windows","w").worker_id=="w"
    assert not lock.exists()

def test_pid_reuse_lock_fingerprint_mismatch_is_reclaimed(tmp_path):
    state=tmp_path/"leases.json"; lock=tmp_path/"leases.json.lock"
    lock.write_text('{"schema_version":1,"pid":77,"fingerprint":"old","token":"dead","acquired_at":1}\n')
    pool=RuntimeLeasePool({"native-windows":1},state,
        fingerprint=lambda pid:"self" if pid==os.getpid() else "new",
        pid_alive=lambda pid:True)
    assert pool.acquire("native-windows","w").worker_id=="w"

def test_live_lock_is_not_stolen(tmp_path):
    state=tmp_path/"leases.json"; lock=tmp_path/"leases.json.lock"
    lock.write_text('{"schema_version":1,"pid":77,"fingerprint":"same","token":"live","acquired_at":1}\n')
    pool=RuntimeLeasePool({"native-windows":1},state,
        fingerprint=lambda pid:"self" if pid==os.getpid() else "same",
        pid_alive=lambda pid:True)
    with pytest.raises(RuntimeError,match="RUNTIME_LEASE_LOCK_TIMEOUT"):
        with pool._locked(timeout=0.03): pass
    assert lock.exists()

def test_heartbeat_requires_current_lease_fencing_token(tmp_path):
    state=tmp_path/"leases.json"; fp=lambda pid:f"p:{pid}"
    pool=RuntimeLeasePool({"native-windows":1},state,fp,lambda pid:True)
    lease=pool.acquire("native-windows","w",owner_pid=77)
    with pytest.raises(RuntimeError,match="FENCE_REJECTED"):
        pool.heartbeat("w","superseded")
    renewed=pool.heartbeat("w",lease.lease_id)
    assert renewed.heartbeat_at>=lease.heartbeat_at
    assert renewed.lease_id==lease.lease_id

def test_superseded_worker_cannot_cross_execution_fence(tmp_path):
    state=tmp_path/"leases.json"; fp=lambda pid:f"p:{pid}"
    pool=RuntimeLeasePool({"native-windows":1},state,fp,lambda pid:True)
    old=pool.acquire("native-windows","w",owner_pid=77)
    pool.release("w")
    current=pool.acquire("native-windows","w",owner_pid=88)
    with pytest.raises(RuntimeError,match="FENCE_REJECTED"):
        pool.assert_fence("w",old.lease_id)
    assert pool.assert_fence("w",current.lease_id)==current
