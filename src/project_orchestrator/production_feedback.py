from __future__ import annotations
import json
from pathlib import Path
from typing import Any,Callable
from .execution_continuation import learn_and_continue
TERMINAL={"PASS","FAIL","NO_OP","BLOCKED","ENVIRONMENT_FAILURE","REVIEW_REQUIRED","PRODUCT_REALITY_REQUIRED"}
def ingest_production_results(*,pending_dir:Path,results_dir:Path,processed_dir:Path,lane_context:dict[str,dict[str,Any]],claude_state_dir:Path,laptop_queue_dir:Path,scorecard:dict|None=None,claude_opener:Callable|None=None)->list[dict[str,Any]]:
 processed_dir.mkdir(parents=True,exist_ok=True);events=[]
 if not results_dir.exists(): return events
 for result_path in sorted(results_dir.glob("*.result.json")):
  result=json.loads(result_path.read_text(encoding="utf-8"));job_id=result.get("job_id")
  if not isinstance(job_id,str): continue
  marker=processed_dir/f"{job_id}.json"
  if marker.exists(): continue
  status=str(result.get("status","UNKNOWN")).upper()
  if status not in TERMINAL: status="UNKNOWN"
  repo=result.get("repository");ctx=lane_context.get(str(repo),{})
  worker={"status":status,"project":ctx.get("project") or ({"jweter/Project-Everward":"everward","jweter/rocksmith-cdlc-generator":"rocksmith"}.get(str(repo),"knowledge-engine")),"repository":repo,
   "head_sha":result.get("exact_head_sha") or result.get("base_sha") or "0"*40,"probe_id":job_id,
   "pattern_id":str(result.get("failure_class") or f"worker-{status.lower()}"),
   "evidence_ref":result.get("evidence_ref") or f"worker://{job_id}","observed_at":ctx.get("observed_at","1970-01-01T00:00:00+00:00"),
   "summary":ctx.get("summary") or f"production Laptop Actor returned {status}"}
  if ctx:
   outcome=learn_and_continue(worker_result=worker,control_plane=ctx.get("control_plane",{}),memory=ctx.get("memory",{}),candidate_tasks=ctx.get("candidate_tasks",[]),claude_state_dir=claude_state_dir,laptop_queue_dir=laptop_queue_dir,prior_events=ctx.get("prior_events"),measurements=ctx.get("measurements"),scorecard=scorecard,claude_opener=claude_opener)
  else: outcome={"status":"UNMAPPED_RESULT","dispatch":None}
  event={"job_id":job_id,"repository":repo,"worker_status":status,"outcome":outcome}
  tmp=marker.with_suffix(".tmp");tmp.write_text(json.dumps(event,sort_keys=True,indent=2)+chr(10),encoding="utf-8");tmp.replace(marker);events.append(event)
 return events
