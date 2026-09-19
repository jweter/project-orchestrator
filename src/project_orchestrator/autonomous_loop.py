from __future__ import annotations
import json,time
from pathlib import Path
from typing import Any,Callable
from .execution_continuation import learn_and_continue
PRODUCTS=("knowledge-engine","rocksmith","everward")
def run_autonomous_cycle(*,lanes:dict[str,dict[str,Any]],claude_state_dir:Path,laptop_queue_dir:Path,telemetry_path:Path,max_dispatches:int=3,scorecard:dict|None=None,claude_opener:Callable|None=None)->dict[str,Any]:
 if max_dispatches<1: raise ValueError("max_dispatches must be positive")
 started=time.time();results={};dispatched=0;eligible_unadvanced=[]
 for product in PRODUCTS:
  lane=lanes.get(product)
  if not lane: continue
  if not lane.get("worker_result"):
   if lane.get("candidate_tasks"): eligible_unadvanced.append(product)
   results[product]={"status":"WAITING_FOR_RESULT"};continue
  out=learn_and_continue(worker_result=lane["worker_result"],control_plane=lane["control_plane"],memory=lane["memory"],candidate_tasks=lane.get("candidate_tasks",[]),claude_state_dir=claude_state_dir,laptop_queue_dir=laptop_queue_dir,prior_events=lane.get("prior_events"),measurements=lane.get("measurements"),scorecard=scorecard,claude_opener=claude_opener)
  if out["status"]=="CONTINUED":
   if dispatched>=max_dispatches:
    out={"status":"SURGE_BUDGET_HELD","dispatch":None,"selected_task":out["selected_task"]};eligible_unadvanced.append(product)
   else: dispatched+=1
  elif out["status"]!="NO_ELIGIBLE_WORK" and lane.get("candidate_tasks"): eligible_unadvanced.append(product)
  results[product]=out
 liveness="P1_LIVENESS_DEFECT" if eligible_unadvanced and dispatched==0 else "HEALTHY"
 snapshot={"started_at":started,"completed_at":time.time(),"dispatches":dispatched,"products":results,"eligible_unadvanced":eligible_unadvanced,"liveness":liveness}
 telemetry_path.parent.mkdir(parents=True,exist_ok=True)
 with telemetry_path.open("a",encoding="utf-8") as f:
  f.write(json.dumps(snapshot,sort_keys=True)+chr(10))
 return snapshot
