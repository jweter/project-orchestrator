import json
from project_orchestrator.autonomous_watchdog import run_watchdog_iteration,run_persistent_watchdog
def stalled(): return {"knowledge-engine":{"candidate_tasks":[{"task_key":"x"}]}}
def args(tmp_path): return dict(state_path=tmp_path/"state.json",telemetry_path=tmp_path/"t.jsonl",claude_state_dir=tmp_path/"c",laptop_queue_dir=tmp_path/"q")
def test_second_consecutive_stall_promotes_p1(tmp_path):
 a=args(tmp_path); first=run_watchdog_iteration(lanes=stalled(),**a); second=run_watchdog_iteration(lanes=stalled(),**a)
 assert first["watchdog"]["status"]=="STALL_OBSERVED"
 assert second["watchdog"]["status"]=="P1_PRODUCTION_STALL"
 assert second["watchdog"]["consecutive_stalls"]==2\n continuation=json.loads((tmp_path/"continuation-request.json").read_text())\n assert continuation["reason"]=="PRODUCTION_STALL"\n assert continuation["trigger"]=="WATCHDOG"
def test_progress_resets_stall_counter(tmp_path):
 a=args(tmp_path); run_watchdog_iteration(lanes=stalled(),**a)
 out=run_watchdog_iteration(lanes={},**a)
 assert out["watchdog"]["status"]=="HEALTHY"; assert out["watchdog"]["consecutive_stalls"]==0
def test_persistent_runner_repeats_without_human_prompt(tmp_path):
 sleeps=[]
 out=run_persistent_watchdog(lane_provider=lambda:{},max_iterations=3,interval_seconds=1,sleep=lambda n:sleeps.append(n),**args(tmp_path))
 assert len(out)==3; assert sleeps==[1,1]
 assert json.loads((tmp_path/"state.json").read_text())["status"]=="HEALTHY"
