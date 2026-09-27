from __future__ import annotations
import json,time
from pathlib import Path
from typing import Any,Callable
from .autonomous_watchdog import run_watchdog_iteration
from .portfolio_readiness import assess_portfolio_readiness,persist_readiness
from .team_runtime import build_team_plan

def _team_candidates(lanes:dict[str,dict[str,Any]])->list[dict[str,Any]]:
 out=[]
 for project,lane in lanes.items():
  for task in lane.get("candidate_tasks",[]):
   if not isinstance(task,dict): continue
   candidate=dict(task)
   candidate.setdefault("project_id",project)
   candidate.setdefault("repository",lane.get("repository"))
   candidate.setdefault("base_sha",lane.get("base_sha"))
   candidate.setdefault("branch",task.get("branch"))
   candidate.setdefault("paths",task.get("paths",[]))
   if all(candidate.get(key) for key in ("project_id","repository","base_sha","branch","title")) and candidate.get("paths"):
    out.append(candidate)
 return out

def run_self_driving_iteration(*,lanes:dict[str,dict[str,Any]],portfolio_results:list[dict[str,Any]],state_dir:Path,
 claude_state_dir:Path,laptop_queue_dir:Path,max_dispatches:int=4,scorecard:dict|None=None,
 claude_opener:Callable|None=None,now:float|None=None)->dict[str,Any]:
 now=time.time() if now is None else now
 readiness=assess_portfolio_readiness(portfolio_results,now=now)
 persist_readiness(readiness,state_dir/"portfolio-readiness.json")
 if not readiness["healthy"]:
  repair={"priority":"P1","kind":"LAPTOP_ACTOR_PORTFOLIO_REPAIR","missing":readiness["missing"],"failed":readiness["failed"],"stale":readiness["stale"]}
  state={"updated_at":now,"status":"P1_REPAIR_REQUIRED","readiness":readiness,"repair":repair,"dispatches":0}
  persist_readiness(state,state_dir/"self-driving-state.json")
  return state
 live_active=[
  {"repository":lane.get("repository"),**work}
  for lane in lanes.values()
  for work in lane.get("active_work",[])
  if isinstance(work,dict)
 ]
 team=build_team_plan(_team_candidates(lanes),live_active_work=live_active)
 watchdog=run_watchdog_iteration(lanes=lanes,state_path=state_dir/"watchdog.json",telemetry_path=state_dir/"autonomous-telemetry.jsonl",
  claude_state_dir=claude_state_dir,laptop_queue_dir=laptop_queue_dir,max_dispatches=max_dispatches,scorecard=scorecard,claude_opener=claude_opener)
 state={"updated_at":now,"status":watchdog["watchdog"]["status"],"readiness":readiness,"repair":None,
  "dispatches":watchdog["cycle"]["dispatches"],"watchdog":watchdog["watchdog"],"cycle":watchdog["cycle"],
  "development_team":{"dispatch_ready":len(team["dispatch"]),"leases":team["leases"],
   "ready_queue_depth":dict(team["ready_queue_depth"]),"roles":team["roles"]}}
 persist_readiness(state,state_dir/"self-driving-state.json")
 return state

def run_self_driving_loop(*,lane_provider:Callable[[],dict[str,dict[str,Any]]],result_provider:Callable[[],list[dict[str,Any]]],state_dir:Path,
 claude_state_dir:Path,laptop_queue_dir:Path,interval_seconds:int=300,max_iterations:int|None=None,sleep:Callable[[float],None]=time.sleep)->None:
 if interval_seconds<1: raise ValueError("interval_seconds must be positive")
 i=0
 while max_iterations is None or i<max_iterations:
  try:
   run_self_driving_iteration(lanes=lane_provider(),portfolio_results=result_provider(),state_dir=state_dir,claude_state_dir=claude_state_dir,laptop_queue_dir=laptop_queue_dir)
  except Exception as exc:
   state={"updated_at":time.time(),"status":"P1_REPAIR_REQUIRED","error_class":type(exc).__name__}
   persist_readiness(state,state_dir/"self-driving-state.json")
  i+=1
  if max_iterations is None or i<max_iterations: sleep(interval_seconds)

\n