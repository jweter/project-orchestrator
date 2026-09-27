from __future__ import annotations
import json,time
from pathlib import Path
from typing import Any,Callable
from .autonomous_loop import run_autonomous_cycle

def run_watchdog_iteration(*,lanes:dict[str,dict[str,Any]],state_path:Path,telemetry_path:Path,
 claude_state_dir:Path,laptop_queue_dir:Path,max_dispatches:int=4,
 scorecard:dict|None=None,claude_opener:Callable|None=None)->dict[str,Any]:
 prior={"consecutive_stalls":0}
 if state_path.exists():
  prior=json.loads(state_path.read_text(encoding="utf-8"))
 cycle=run_autonomous_cycle(lanes=lanes,claude_state_dir=claude_state_dir,
  laptop_queue_dir=laptop_queue_dir,telemetry_path=telemetry_path,max_dispatches=max_dispatches,
  scorecard=scorecard,claude_opener=claude_opener)
 stalled=bool(cycle["eligible_unadvanced"]) and cycle["dispatches"]==0
 count=int(prior.get("consecutive_stalls",0))+1 if stalled else 0
 status="P1_REPAIR_REQUIRED" if count>=2 else ("STALL_OBSERVED" if stalled else "HEALTHY")
 state={"updated_at":time.time(),"consecutive_stalls":count,"status":status,
  "eligible_unadvanced":cycle["eligible_unadvanced"],"last_dispatches":cycle["dispatches"]}
 state_path.parent.mkdir(parents=True,exist_ok=True)
 state_path.write_text(json.dumps(state,sort_keys=True,indent=2),encoding="utf-8")
 return {"cycle":cycle,"watchdog":state}

def run_persistent_watchdog(*,lane_provider:Callable[[],dict[str,dict[str,Any]]],state_path:Path,
 telemetry_path:Path,claude_state_dir:Path,laptop_queue_dir:Path,interval_seconds:int=300,
 max_iterations:int|None=None,sleep:Callable[[float],None]=time.sleep)->list[dict[str,Any]]:
 if interval_seconds<1: raise ValueError("interval_seconds must be positive")
 results=[];iteration=0
 while max_iterations is None or iteration<max_iterations:
  results.append(run_watchdog_iteration(lanes=lane_provider(),state_path=state_path,
   telemetry_path=telemetry_path,claude_state_dir=claude_state_dir,laptop_queue_dir=laptop_queue_dir))
  iteration+=1
  if max_iterations is None or iteration<max_iterations: sleep(interval_seconds)
 return results
