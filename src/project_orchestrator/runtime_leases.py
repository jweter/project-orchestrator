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
    heartbeat_at:float

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
                win_dll=getattr(ctypes,"WinDLL")
                k=win_dll("kernel32",use_last_error=True)
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
        if os.name=="nt":
            try:
                import ctypes
                from ctypes import wintypes
                win_dll=getattr(ctypes,"WinDLL")
                k=win_dll("kernel32",use_last_error=True)
                h=k.OpenProcess(0x1000,False,pid)  # PROCESS_QUERY_LIMITED_INFORMATION
                if not h:return False
                code=wintypes.DWORD()
                try:
                    if not k.GetExitCodeProcess(h,ctypes.byref(code)):return False
                    return code.value==259  # STILL_ACTIVE
                finally:k.CloseHandle(h)
            except (AttributeError,OSError):return False
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
        owner_pid=os.getpid()
        owner_fp=self._fingerprint(owner_pid)
        if not owner_fp:
            raise RuntimeError("RUNTIME_LOCK_OWNER_FINGERPRINT_UNAVAILABLE")
        token=uuid.uuid4().hex
        payload={"schema_version":1,"pid":owner_pid,"fingerprint":owner_fp,
                 "token":token,"acquired_at":time.time()}
        while True:
            try:
                fd=os.open(str(lock),os.O_CREAT|os.O_EXCL|os.O_WRONLY)
                os.write(fd,(json.dumps(payload,sort_keys=True)+"\n").encode()); os.close(fd)
                break
            except FileExistsError:
                stale=False
                try:
                    current=json.loads(lock.read_text(encoding="utf-8"))
                    pid=int(current["pid"]); fp=current["fingerprint"]
                    alive=self._pid_alive(pid)
                    stale=(not alive) or self._fingerprint(pid)!=fp
                except (OSError,json.JSONDecodeError,KeyError,TypeError,ValueError):
                    stale=False
                if stale:
                    try: lock.unlink()
                    except FileNotFoundError: pass
                    continue
                if time.monotonic()>=deadline:
                    raise RuntimeError("RUNTIME_LEASE_LOCK_TIMEOUT")
                time.sleep(0.02)
        try:
            yield
        finally:
            try:
                current=json.loads(lock.read_text(encoding="utf-8"))
                if current.get("token")==token:
                    lock.unlink()
            except (FileNotFoundError,OSError,json.JSONDecodeError):
                pass

    def _reload(self):
        self._leases.clear()
        self._load()

    def _load(self):
        if not self.state_path or not self.state_path.exists(): return
        try: raw=json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError,json.JSONDecodeError,TypeError) as exc:
            raise RuntimeError("RUNTIME_LEASE_STATE_INVALID") from exc
        if not isinstance(raw,dict) or raw.get("schema_version") not in {1,2}:
            raise RuntimeError("RUNTIME_LEASE_STATE_INVALID")
        records=raw.get("leases")
        if not isinstance(records,list):
            raise RuntimeError("RUNTIME_LEASE_STATE_INVALID")
        loaded={}
        for x in records:
            try:
                lease=RuntimeLease(x["runtime_id"],x["worker_id"],int(x["owner_pid"]),
                    x["owner_fingerprint"],x["lease_id"],float(x["acquired_at"]),
                    float(x.get("heartbeat_at",x["acquired_at"])))
            except (KeyError,TypeError,ValueError) as exc:
                raise RuntimeError("RUNTIME_LEASE_STATE_INVALID") from exc
            if (not lease.worker_id or not lease.lease_id or not lease.owner_fingerprint
                    or lease.owner_pid<=0 or lease.runtime_id not in self._capacities
                    or lease.worker_id in loaded):
                raise RuntimeError("RUNTIME_LEASE_STATE_INVALID")
            loaded[lease.worker_id]=lease
        self._leases.update(loaded)

    @classmethod
    def recover_invalid_state(cls,capacities:dict[str,int],state_path:Path,*,fingerprint=None,pid_alive=None)->dict:
        """Quarantine invalid authority only when no plausible live owner can be proven."""
        fingerprint=fingerprint or cls._default_fingerprint
        pid_alive=pid_alive or cls._default_pid_alive
        if not state_path.exists():
            return {"status":"NO_STATE","quarantine":None}
        try:
            cls(capacities,state_path,fingerprint,pid_alive)
            return {"status":"VALID","quarantine":None}
        except RuntimeError as exc:
            if str(exc)!="RUNTIME_LEASE_STATE_INVALID":
                raise
        try:
            raw=json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError,json.JSONDecodeError,TypeError):
            raw=None
        records=raw.get("leases",[]) if isinstance(raw,dict) else []
        if isinstance(records,list):
            for record in records:
                if not isinstance(record,dict): continue
                try: pid=int(record.get("owner_pid",0))
                except (TypeError,ValueError): pid=0
                expected=record.get("owner_fingerprint")
                if pid>0 and expected and pid_alive(pid) and fingerprint(pid)==expected:
                    raise RuntimeError("RUNTIME_LEASE_RECOVERY_LIVE_OWNER")
        quarantine=state_path.with_name(f"{state_path.name}.quarantine-{int(time.time())}-{uuid.uuid4().hex[:8]}")
        state_path.replace(quarantine)
        audit=state_path.with_suffix(state_path.suffix+".recovery.jsonl")
        event={"schema_version":1,"event":"RUNTIME_LEASE_STATE_QUARANTINED",
               "quarantine":str(quarantine),"recovered_at":time.time()}
        with audit.open("a",encoding="utf-8") as handle:
            handle.write(json.dumps(event,sort_keys=True)+"\n")
        pool=cls(capacities,state_path,fingerprint,pid_alive)
        pool._persist()
        return {"status":"RECOVERED","quarantine":str(quarantine)}

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
            now=time.time()
            lease=RuntimeLease(runtime_id,worker_id,pid,fp,uuid.uuid4().hex,now,now)
            self._leases[worker_id]=lease; self._persist()
            return lease

    def assert_fence(self,worker_id:str,lease_id:str)->RuntimeLease:
        """Prove the caller still owns the authoritative runtime lease."""
        with self._locked():
            if self.state_path:self._reload()
            lease=self._leases.get(worker_id)
            if lease is None or lease.lease_id!=lease_id:
                raise RuntimeError("RUNTIME_LEASE_FENCE_REJECTED")
            if not self._pid_alive(lease.owner_pid) or self._fingerprint(lease.owner_pid)!=lease.owner_fingerprint:
                raise RuntimeError("RUNTIME_LEASE_FENCE_REJECTED")
            return lease

    def heartbeat(self,worker_id:str,lease_id:str)->RuntimeLease:
        """Renew liveness only when the caller presents the current fencing token."""
        with self._locked():
            if self.state_path:self._reload()
            lease=self._leases.get(worker_id)
            if lease is None or lease.lease_id!=lease_id:
                raise RuntimeError("RUNTIME_LEASE_FENCE_REJECTED")
            if not self._pid_alive(lease.owner_pid) or self._fingerprint(lease.owner_pid)!=lease.owner_fingerprint:
                raise RuntimeError("RUNTIME_LEASE_FENCE_REJECTED")
            renewed=RuntimeLease(lease.runtime_id,lease.worker_id,lease.owner_pid,
                lease.owner_fingerprint,lease.lease_id,lease.acquired_at,time.time())
            self._leases[worker_id]=renewed; self._persist()
            return renewed

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

DEFAULT_RUNTIME_CAPACITIES={"native-windows":6,"laptop-actor":6,"resource:ollama":1,"resource:unreal":1}
