from __future__ import annotations
import hashlib,json
from pathlib import Path
from typing import Any
from .laptop_actor import build_laptop_job

def task_to_packet(task:dict[str,Any])->dict[str,Any]:
 required=("repository","task_key","base_sha","branch","verification")
 missing=[k for k in required if not task.get(k)]
 if missing: raise ValueError(f"live Laptop Actor task missing: {missing}")
 seed=f"{task['repository']}|{task['task_key']}|{task['base_sha']}"
 return {"schema_version":1,"packet_id":"live-"+hashlib.sha256(seed.encode()).hexdigest()[:20],
  "project":task.get("project") or task["repository"].split("/")[-1],
  "repository":task["repository"],"source_ref":task.get("source_ref",task["task_key"]),
  "base_sha":task["base_sha"],"branch":task["branch"],"task":task.get("prompt") or task["task_key"],
  "paths":task.get("paths",[]),"verification":task["verification"]}

def enqueue_live_laptop_task(task:dict[str,Any],*,pending_dir:Path,workspace_root:str)->dict[str,Any]:
 packet=task_to_packet(task); job=build_laptop_job(packet,workspace_root=workspace_root)
 pending_dir.mkdir(parents=True,exist_ok=True); path=pending_dir/f"{job['job_id']}.json"
 if path.exists(): return {"status":"ALREADY_QUEUED","job_id":job["job_id"],"path":str(path)}
 tmp=path.with_suffix(".tmp");tmp.write_text(json.dumps(job,sort_keys=True,indent=2)+chr(10),encoding="utf-8");tmp.replace(path)
 return {"status":"QUEUED","job_id":job["job_id"],"path":str(path),"packet_id":job["packet_id"]}
