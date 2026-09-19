import json
from project_orchestrator.live_laptop_dispatch import enqueue_live_laptop_task
SHA="a"*40
def task():
 return {"project":"everward","repository":"jweter/Project-Everward","task_key":"issue-270-contract-probe",
 "base_sha":SHA,"branch":"orchestrator/issue-270-world-persistence","prompt":"verify authoritative generated-region persistence contract",
 "paths":[],"verification":["git status --short"]}
def test_live_task_becomes_runner_compatible_job(tmp_path):
 out=enqueue_live_laptop_task(task(),pending_dir=tmp_path/"pending",workspace_root=str(tmp_path/"work"))
 job=json.loads(next((tmp_path/"pending").glob("*.json")).read_text())
 assert out["status"]=="QUEUED";assert job["repository"]=="jweter/Project-Everward"
 assert job["base_sha"]==SHA;assert job["verification"]==["git status --short"]
 assert job["prohibitions"]["merge"] is True
def test_live_queue_is_idempotent(tmp_path):
 a=enqueue_live_laptop_task(task(),pending_dir=tmp_path/"p",workspace_root=str(tmp_path/"w"))
 b=enqueue_live_laptop_task(task(),pending_dir=tmp_path/"p",workspace_root=str(tmp_path/"w"))
 assert a["job_id"]==b["job_id"];assert b["status"]=="ALREADY_QUEUED";assert len(list((tmp_path/"p").glob("*.json")))==1
