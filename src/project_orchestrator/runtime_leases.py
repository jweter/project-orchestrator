from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class RuntimeLease:
    runtime_id:str
    worker_id:str

class RuntimeLeasePool:
    """In-process capacity authority for execution runtimes."""
    def __init__(self,capacities:dict[str,int]):
        if any(not k or v<1 for k,v in capacities.items()):
            raise ValueError("runtime capacities must be positive")
        self._capacities=dict(capacities)
        self._leases:dict[str,RuntimeLease]={}

    def running(self,runtime_id:str)->int:
        return sum(1 for lease in self._leases.values() if lease.runtime_id==runtime_id)

    def acquire(self,runtime_id:str,worker_id:str)->RuntimeLease:
        if runtime_id not in self._capacities:
            raise RuntimeError(f"RUNTIME_CAPACITY_UNKNOWN:{runtime_id}")
        if worker_id in self._leases:
            raise RuntimeError(f"RUNTIME_LEASE_ALREADY_HELD:{worker_id}")
        if self.running(runtime_id)>=self._capacities[runtime_id]:
            raise RuntimeError(f"RUNTIME_SATURATED:{runtime_id}")
        lease=RuntimeLease(runtime_id,worker_id)
        self._leases[worker_id]=lease
        return lease

    def release(self,worker_id:str)->bool:
        return self._leases.pop(worker_id,None) is not None

    def snapshot(self)->dict:
        return {"capacities":dict(self._capacities),
                "running":{rid:self.running(rid) for rid in self._capacities},
                "leases":[{"runtime_id":x.runtime_id,"worker_id":x.worker_id}
                          for x in sorted(self._leases.values(),key=lambda x:x.worker_id)]}

DEFAULT_RUNTIME_CAPACITIES={"native-windows":6,"laptop-actor":1}
