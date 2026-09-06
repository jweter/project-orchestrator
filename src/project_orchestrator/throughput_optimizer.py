from __future__ import annotations
from typing import Any

MIN_WINDOWS_PER_PROJECT=3
COMPONENT_CAP=10.0

def _measured(project:dict[str,Any],key:str)->dict[str,Any]|None:
    metric=project.get("metrics",{}).get(key)
    return metric if isinstance(metric,dict) and metric.get("status")=="MEASURED" else None

def _seconds_score(metric:dict[str,Any]|None)->float|None:
    if metric is None: return None
    return min(COMPONENT_CAP,float(metric["value"])/3600.0)

def _count_score(metric:dict[str,Any]|None)->float|None:
    if metric is None: return None
    return min(COMPONENT_CAP,float(metric["value"])*2.0)

def optimize_throughput(snapshot:dict[str,Any],*,eligible_projects:list[str]|None=None)->dict[str,Any]:
    """Rank measured throughput bottlenecks; never predicts gains or executes work."""
    allowed=set(eligible_projects) if eligible_projects is not None else None
    rows=[]
    for project_id,project in sorted(snapshot.get("by_project",{}).items()):
        if project_id=="portfolio" or (allowed is not None and project_id not in allowed):
            continue
        windows=int(project.get("window_count",0))
        components={
            "merge_cycle_burden":_seconds_score(_measured(project,"merge_cycle_seconds")),
            "failure_repair_burden":_seconds_score(_measured(project,"failure_to_repair_seconds")),
            "ci_failure_burden":_count_score(_measured(project,"ci_failure_count")),
            "repeat_failure_burden":_count_score(_measured(project,"ci_repeat_failure_count")),
        }
        measured={k:v for k,v in components.items() if v is not None}
        refs=sorted({ref for key in ["merge_cycle_seconds","failure_to_repair_seconds","ci_failure_count","ci_repeat_failure_count"]
                     for metric in [_measured(project,key)] if metric for ref in metric.get("evidence_refs",[])})
        if windows<MIN_WINDOWS_PER_PROJECT:
            state="INSUFFICIENT_DATA"; reason=f"{windows} evidence window(s); need {MIN_WINDOWS_PER_PROJECT}"
            score=None
        elif len(measured)<2:
            state="INSUFFICIENT_DATA"; reason="fewer than two measured throughput-bottleneck dimensions"
            score=None
        else:
            state="READY"; reason="sufficient measured evidence for ordinal bottleneck ranking"
            score=sum(measured.values())/len(measured)
        rows.append({"project_id":project_id,"window_count":windows,"state":state,"reason":reason,
                     "measured_components":measured,"unknown_components":sorted(set(components)-set(measured)),
                     "ordinal_bottleneck_score":score,"evidence_refs":refs})
    ready=[r for r in rows if r["state"]=="READY"]
    ready.sort(key=lambda r:(-r["ordinal_bottleneck_score"],r["project_id"]))
    selected=ready[0]["project_id"] if ready else None
    return {"schema_version":1,"mode":"recommendation_only","selected_project":selected,
            "selection_state":"READY" if selected else "INSUFFICIENT_DATA","projects":rows,
            "score_semantics":"ordinal measured bottleneck severity only; not predicted gain, ROI, probability, capacity percentage, or percent complete",
            "safety_boundaries":["uses only MEASURED evidence","UNKNOWN and INSUFFICIENT_DATA never become zero",
            "optimizer ranks only currently eligible project lanes","selection never overrides P0-P4 priority, product authority, Product Reality, specialist ownership, preflight, CI, or central merge arbitration",
            "no automatic execution or merge authority","no exponential-development claim without sustained comparable evidence"]}
