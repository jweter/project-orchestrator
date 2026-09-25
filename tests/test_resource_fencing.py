import pytest
from pathlib import Path
from project_orchestrator.runtime_leases import RuntimeLeasePool
import scripts.autonomous_slice_builder as builder

def test_resource_fence_revocation_blocks_next_runtime_command(tmp_path,monkeypatch):
    fp=lambda pid:f"p:{pid}"
    pool=RuntimeLeasePool({"laptop-actor":2,"resource:ollama":1},tmp_path/"leases.json",fp,lambda pid:True)
    runtime=pool.acquire("laptop-actor","w",owner_pid=77)
    resource=pool.acquire("resource:ollama","w:resource:ollama",owner_pid=77)
    builder.ACTIVE_RUNTIME_FENCE=(pool,"w",runtime.lease_id)
    builder.ACTIVE_RESOURCE_FENCES=[("w:resource:ollama",resource.lease_id)]
    pool.release("w:resource:ollama")
    with pytest.raises(RuntimeError,match="FENCE_REJECTED"):
        builder.run(["never-runs"],tmp_path)
    builder.ACTIVE_RUNTIME_FENCE=None; builder.ACTIVE_RESOURCE_FENCES=[]

def test_resource_heartbeat_renews_authority(tmp_path):
    fp=lambda pid:f"p:{pid}"
    pool=RuntimeLeasePool({"laptop-actor":2,"resource:ollama":1},tmp_path/"leases.json",fp,lambda pid:True)
    resource=pool.acquire("resource:ollama","w:resource:ollama",owner_pid=77)
    renewed=pool.heartbeat("w:resource:ollama",resource.lease_id)
    assert renewed.lease_id==resource.lease_id
    assert renewed.heartbeat_at>=resource.heartbeat_at
