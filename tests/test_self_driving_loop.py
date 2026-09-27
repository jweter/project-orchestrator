from project_orchestrator.self_driving_loop import run_self_driving_iteration,run_self_driving_loop
from project_orchestrator.portfolio_laptop_probe import PORTFOLIO
def ready(now=1000): return [{"repository":r,"status":"PASS","completed_at":now} for _,r in PORTFOLIO]
def test_not_ready_becomes_p1_before_product_dispatch(tmp_path):
 rows=ready()[:-1];out=run_self_driving_iteration(lanes={},portfolio_results=rows,state_dir=tmp_path/"s",claude_state_dir=tmp_path/"c",laptop_queue_dir=tmp_path/"q",now=1000)
 assert out["status"]=="P1_REPAIR_REQUIRED";assert out["dispatches"]==0;assert out["repair"]["missing"]==["jweter/VoiceStudio"]
def test_ready_portfolio_enters_scheduler(tmp_path):
 out=run_self_driving_iteration(lanes={},portfolio_results=ready(),state_dir=tmp_path/"s",claude_state_dir=tmp_path/"c",laptop_queue_dir=tmp_path/"q",now=1000)
 assert out["readiness"]["healthy"] is True;assert out["status"]=="HEALTHY"
def test_loop_contains_iteration_exception_and_persists_p1(tmp_path):
 def boom(): raise RuntimeError("secret details")
 run_self_driving_loop(lane_provider=boom,result_provider=lambda:ready(),state_dir=tmp_path/"s",claude_state_dir=tmp_path/"c",laptop_queue_dir=tmp_path/"q",max_iterations=1,interval_seconds=1)
 import json
 state=json.loads((tmp_path/"s"/"self-driving-state.json").read_text());assert state["status"]=="P1_REPAIR_REQUIRED";assert state["error_class"]=="RuntimeError";assert "secret" not in str(state)


def test_ready_loop_projects_parallel_team_contracts_without_dispatching_merge_authority(tmp_path):
 lanes={
  "rocksmith":{
   "repository":"jweter/rocksmith-cdlc-generator","base_sha":"a"*40,
   "candidate_tasks":[
    {"title":"EOF worker A","branch":"feature/a","paths":["src/a.py"],"priority":1},
    {"title":"EOF worker B","branch":"feature/b","paths":["tests/b.py"],"priority":2},
   ],
  }
 }
 out=run_self_driving_iteration(lanes=lanes,portfolio_results=ready(),state_dir=tmp_path/"s",claude_state_dir=tmp_path/"c",laptop_queue_dir=tmp_path/"q",now=1000)
 team=out["development_team"]
 assert team["dispatch_ready"]==2
 assert team["ready_queue_depth"]["rocksmith"]==2
 assert team["roles"]["merge_authority"]=="existing central merge authority only"
 assert all(lease["persistent_ownership"] is False for lease in team["leases"])

def test_team_projection_respects_live_path_ownership(tmp_path):
 lanes={
  "everward":{
   "repository":"jweter/Project-Everward","base_sha":"b"*40,
   "active_work":[{"branch":"feature/live","changed_files":["src/simulation"]}],
   "candidate_tasks":[
    {"title":"conflicting sim work","branch":"feature/new","paths":["src/simulation/a.cpp"],"priority":1}
   ],
  }
 }
 out=run_self_driving_iteration(lanes=lanes,portfolio_results=ready(),state_dir=tmp_path/"s",claude_state_dir=tmp_path/"c",laptop_queue_dir=tmp_path/"q",now=1000)
 assert out["development_team"]["dispatch_ready"]==0
 assert out["development_team"]["leases"][0]["state"]=="CONFLICT"
