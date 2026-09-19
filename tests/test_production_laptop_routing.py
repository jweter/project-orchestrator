import json
from project_orchestrator.execution_dispatch import execute_actor_selection
SHA="a"*40
def task(tmp_path):
 return {"repository":"jweter/Project-Everward","task_key":"ev-live-probe","kind":"PRODUCT_SLICE","local_capability_verified":True,
 "base_sha":SHA,"branch":"orchestrator/issue-270-world-persistence","verification":["git status --short"],
 "workspace_root":str(tmp_path/"work"),"prompt":"verify persistence contract"}
def test_selected_local_task_becomes_production_job(tmp_path):
 out=execute_actor_selection(task(tmp_path),claude_state_dir=tmp_path/"c",laptop_queue_dir=tmp_path/"pending")
 assert out["actor"]=="LAPTOP_ACTOR";assert out["status"]=="QUEUED"
 job=json.loads(next((tmp_path/"pending").glob("*.json")).read_text())
 assert job["base_sha"]==SHA;assert job["prohibitions"]["merge"] is True
def test_production_route_is_idempotent(tmp_path):
 a=execute_actor_selection(task(tmp_path),claude_state_dir=tmp_path/"c",laptop_queue_dir=tmp_path/"pending")
 b=execute_actor_selection(task(tmp_path),claude_state_dir=tmp_path/"c",laptop_queue_dir=tmp_path/"pending")
 assert a["job_id"]==b["job_id"];assert b["status"]=="ALREADY_QUEUED"
