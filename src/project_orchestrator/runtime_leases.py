from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json, os, time, uuid
from contextlib import contextmanager

@dataclass(frozen=True)
class RuntimeLease:
    runtime_id:str
    worker_id:str
    owner_pid:int
    owner_fingerprint:str
    lease_id:str
    acquired_at:float

class RuntimeLeasePool:
    """Durable capacity authority for execution runtimes."""
    def __init__(self,capacities:dict[str,int],state_path:Path|None=None,
                 fingerprint=None,pid_alive=None):
        if any(not k or v<1 for k,v in capacities.items()):
            raise ValueError("runtime capacities must be positive")
        self._capacities=dict(capacities)
        self.state_path=state_path
        self._fingerprint=fingerprint or self._default_fingerprint
        self._pid_alive=pid_alive or self._default_pid_alive
        self._leases:dict[str,RuntimeLease]={}
        self._load()
        self.recover_stale()

    @staticmethod
    def _default_fingerprint(pid:int)->str|None:
        """OS-backed process creation identity prevents PID-reuse false ownership."""
        if os.name=="nt":
            try:
                import ctypes
                from ctypes import wintypes
                k=ctypes.WinDLL("kernel32",use_last_error=True)
                h=k.OpenProcess(0x1000,False,pid)
                if not h:return None
                c=wintypes.FILETIME(); e=wintypes.FILETIME(); kt=wintypes.FILETIME(); u=wintypes.FILETIME()
                try:
                    if not k.GetProcessTimes(h,ctypes.byref(c),ctypes.byref(e),ctypes.byref(kt),ctypes.byref(u)):return None
                    return f"windows:{(c.dwHighDateTime<<32)|c.dwLowDateTime}"
                finally:k.CloseHandle(h)
            except (AttributeError,OSError):return None
        try:
            stat=Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            start=stat[stat.rfind(")")+2:].split()[19]
            boot=Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
            return f"linux:{boot}:{start}"
        except (OSError,IndexError,ValueError):return None

    @staticmethod
    def _default_pid_alive(pid:int)->bool:
        if pid<=0:return False
        try: os.kill(pid,0); return True
        except (OSError,PermissionError): return False

    @contextmanager
    def _locked(self,timeout:float=5.0):
        """Cross-process lock serializes read/check/write lease transactions."""
        if not self.state_path:
            yield; return
        lock=self.state_path.with_suffix(self.state_path.suffix+".lock")
        lock.parent.mkdir(parents=True,exist_ok=True)
        deadline=time.monotonic()+timeout
        while True:
            try:
                fd=os.open(str(lock),os.O_CREAT|os.O_EXCL|os.O_WRONLY)
                os.write(fd,f"{os.getpid()}\n".encode()); os.close(fd)
                break
            except FileExistsError:
                if time.monotonic()>=deadline:
                    raise RuntimeError("RUNTIME_LEASE_LOCK_TIMEOUT")
                time.sleep(0.02)
        try:
            yield
        finally:
            try: lock.unlink()
            except FileNotFoundError: pass

    def _reload(self):
        self._leases.clear()
        self._load()

    def _load(self):
        if not self.state_path or not self.state_path.exists(): return
        try: raw=json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError,json.JSONDecodeError,TypeError): return
        for x in raw.get("leases",[]):
            try:
                lease=RuntimeLease(x["runtime_id"],x["worker_id"],int(x["owner_pid"]),
                    x["owner_fingerprint"],x["lease_id"],float(x["acquired_at"]))
                self._leases[lease.worker_id]=lease
            except (KeyError,TypeError,ValueError): continue

    def _persist(self):
        if not self.state_path:return
        self.state_path.parent.mkdir(parents=True,exist_ok=True)
        tmp=self.state_path.with_suffix(self.state_path.suffix+".tmp")
        tmp.write_text(json.dumps(self.snapshot(),indent=2,sort_keys=True)+"\n",encoding="utf-8")
        tmp.replace(self.state_path)

    def running(self,runtime_id:str)->int:
        return sum(1 for x in self._leases.values() if x.runtime_id==runtime_id)

    def recover_stale(self)->list[str]:
        removed=[]
        for worker,lease in list(self._leases.items()):
            alive=self._pid_alive(lease.owner_pid)
            fingerprint=self._fingerprint(lease.owner_pid) if alive else None
            if not alive or fingerprint!=lease.owner_fingerprint:
                del self._leases[worker]; removed.append(worker)
        if removed:self._persist()
        return removed

    def acquire(self,runtime_id:str,worker_id:str,owner_pid:int|None=None)->RuntimeLease:
        with self._locked():
            if self.state_path:
                self._reload()
            self.recover_stale()
            if runtime_id not in self._capacities:
                raise RuntimeError(f"RUNTIME_CAPACITY_UNKNOWN:{runtime_id}")
            if worker_id in self._leases:
                raise RuntimeError(f"RUNTIME_LEASE_ALREADY_HELD:{worker_id}")
            if self.running(runtime_id)>=self._capacities[runtime_id]:
                raise RuntimeError(f"RUNTIME_SATURATED:{runtime_id}")
            pid=owner_pid or os.getpid()
            fp=self._fingerprint(pid)
            if not fp: raise RuntimeError("RUNTIME_OWNER_FINGERPRINT_UNAVAILABLE")
            lease=RuntimeLease(runtime_id,worker_id,pid,fp,uuid.uuid4().hex,time.time())
            self._leases[worker_id]=lease; self._persist()
            return lease

    def release(self,worker_id:str)->bool:
        with self._locked():
            if self.state_path:
                self._reload()
            removed=self._leases.pop(worker_id,None) is not None
            if removed:self._persist()
            return removed

    def snapshot(self)->dict:
        return {"schema_version":2,"capacities":dict(self._capacities),
          "running":{rid:self.running(rid) for rid in self._capacities},
          "leases":[x.__dict__ for x in sorted(self._leases.values(),key=lambda x:x.worker_id)]}

DEFAULT_RUNTIME_CAPACITIES={"native-windows":6,"laptop-actor":1}
