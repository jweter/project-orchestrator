import json
from project_orchestrator.autonomous_loop import run_autonomous_cycle
SHA="a"*40
def lane(repo,project,key):
 return {"worker_result":{"project":project,"repository":repo,"head_sha":SHA,"probe_id":"p","pattern_id":"pat","evidence_ref":"ci","observed_at":"2026-09-19T00:00:00Z","status":"PASS","summary":"ok"},
 "control_plane":{"schema_version":1,"repositories":{repo:{"safe_repairs":[]}}},"memory":{"schema_version":1,"entries":[]},
 "candidate_tasks":[{"repository":repo,"task_key":key,"kind":"implementation","priority":1}]}
def test_surge_advances_all_four_products(tmp_path):
 lanes={"knowledge-engine":lane("jweter/knowledge-engine-core","knowledge-engine","ke"),
 "rocksmith":lane("jweter/rocksmith-cdlc-generator","rocksmith","rs"),
 "everward":lane("jweter/Project-Everward","everward","ev"),
 "voicestudio":lane("jweter/VoiceStudio","voicestudio","vs")}
 out=run_autonomous_cycle(lanes=lanes,claude_state_dir=tmp_path/"c",laptop_queue_dir=tmp_path/"q",telemetry_path=tmp_path/"telemetry.jsonl")
 assert out["dispatches"]==4; assert out["liveness"]=="HEALTHY"
 assert all(out["products"][p]["status"]=="CONTINUED" for p in lanes)
def test_no_progress_with_eligible_work_is_p1(tmp_path):
 lanes={"knowledge-engine":{"candidate_tasks":[{"task_key":"x"}]}}
 out=run_autonomous_cycle(lanes=lanes,claude_state_dir=tmp_path/"c",laptop_queue_dir=tmp_path/"q",telemetry_path=tmp_path/"t")
 assert out["liveness"]=="P1_LIVENESS_DEFECT"; assert out["eligible_unadvanced"]==["knowledge-engine"]
def test_cycle_persists_liveness_telemetry(tmp_path):
 path=tmp_path/"telemetry.jsonl"
 out=run_autonomous_cycle(lanes={},claude_state_dir=tmp_path/"c",laptop_queue_dir=tmp_path/"q",telemetry_path=path)
 saved=json.loads(path.read_text().splitlines()[-1]); assert saved["liveness"]=="HEALTHY"; assert saved["dispatches"]==0
