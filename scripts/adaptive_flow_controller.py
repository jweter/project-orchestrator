"""Pure scheduling policy for adaptive continuous-flow capacity."""
from __future__ import annotations
from dataclasses import asdict, dataclass

@dataclass(frozen=True)
class ResourceSnapshot:
    cpu_percent: float
    available_memory_mb: int
    total_memory_mb: int
    local_builder_busy: bool = False

def launch_decision(snapshot:ResourceSnapshot)->dict:
    reasons=[]
    if snapshot.available_memory_mb < 3072:
        reasons.append("LOW_AVAILABLE_MEMORY")
    if snapshot.cpu_percent >= 90.0:
        reasons.append("CPU_SATURATED")
    if snapshot.local_builder_busy:
        reasons.append("LOCAL_BUILDER_BUSY")
    allowed=not reasons
    return {
        "launch_heavy_builder":allowed,
        "heavy_builder_capacity":1 if allowed else 0,
        "verification_capacity":2 if snapshot.available_memory_mb>=4096 and snapshot.cpu_percent<85 else 1,
        "reasons":reasons,
        "snapshot":asdict(snapshot),
    }

def next_delay(*,decision:dict,builder_status:str,lifecycle_advanced:bool,normal_seconds:int)->int:
    if lifecycle_advanced:
        return 1
    if decision.get("launch_heavy_builder") is False:
        return 30
    immediate={"PR_OPEN","REVIEW_BLOCKED","CI_PENDING","PR_BLOCKED",
               "PREFLIGHT_FAILED","AUTONOMOUS_CHANGE_REJECTED","SELF_REPAIRED_TO_NO_OP"}
    if builder_status in immediate:
        return 1
    return max(30,min(normal_seconds,300))
