"""Adaptive scheduling policy for the continuous laptop flow."""
from __future__ import annotations
from dataclasses import dataclass, asdict

@dataclass(frozen=True)
class ResourceSnapshot:
    cpu_percent: float
    available_memory_mb: int
    total_memory_mb: int
    ollama_busy: bool = False
    aider_busy: bool = False

def launch_decision(s:ResourceSnapshot, *, user_active:bool=False)->dict:
    reasons=[]
    if user_active:
        reasons.append("USER_ACTIVE")
    if s.available_memory_mb < 3072:
        reasons.append("LOW_AVAILABLE_MEMORY")
    if s.cpu_percent >= 90.0:
        reasons.append("CPU_SATURATED")
    if s.aider_busy:
        reasons.append("AIDER_ALREADY_ACTIVE")
    allowed=not reasons
    return {
        "launch_heavy_builder":allowed,
        "heavy_builder_capacity":1 if allowed else 0,
        "verification_capacity":2 if s.available_memory_mb>=4096 and s.cpu_percent<85 else 1,
        "reasons":reasons,
        "snapshot":asdict(s),
    }

def adaptive_delay(*, decision:dict, builder_status:str,
                   lifecycle_advanced:bool, normal_seconds:int)->int:
    if lifecycle_advanced:
        return 1
    if decision.get("launch_heavy_builder") is False:
        reasons=set(decision.get("reasons",[]))
        if "USER_ACTIVE" in reasons:
            return min(max(normal_seconds,60),300)
        return 30
    immediate={"PR_OPEN","REVIEW_BLOCKED","CI_PENDING","PR_BLOCKED",
               "PREFLIGHT_FAILED","AUTONOMOUS_CHANGE_REJECTED","SELF_REPAIRED_TO_NO_OP"}
    if builder_status in immediate:
        return 1
    return max(30,min(normal_seconds,300))

def snapshot_from_metrics(cpu_percent:float, available_memory_mb:int,
                          total_memory_mb:int)->ResourceSnapshot:
    return ResourceSnapshot(float(cpu_percent),int(available_memory_mb),int(total_memory_mb))

def sample_windows_memory()->tuple[int,int]:
    import ctypes
    class MemoryStatus(ctypes.Structure):
        _fields_=[("length",ctypes.c_ulong),("load",ctypes.c_ulong),
                  ("total_phys",ctypes.c_ulonglong),("avail_phys",ctypes.c_ulonglong),
                  ("total_page",ctypes.c_ulonglong),("avail_page",ctypes.c_ulonglong),
                  ("total_virtual",ctypes.c_ulonglong),("avail_virtual",ctypes.c_ulonglong),
                  ("avail_extended",ctypes.c_ulonglong)]
    status=MemoryStatus()
    status.length=ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise OSError("memory status unavailable")
    unit=1024*1024
    return int(status.avail_phys/unit),int(status.total_phys/unit)

def sample_resources(cpu_percent:float=0.0)->ResourceSnapshot:
    available,total=sample_windows_memory()
    return ResourceSnapshot(cpu_percent=float(cpu_percent),
                            available_memory_mb=available,total_memory_mb=total)
