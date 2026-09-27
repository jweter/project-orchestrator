from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from .planning_dispatch import PRODUCT_BY_REPOSITORY, product_for_repository
from .kpi_telemetry import KpiTelemetryError, build_rolling_reports, load_events
from .grant_lifecycle import canonical_job_projection
from .state_io import atomic_json

def planner_decision_quality(state_root:Path)->dict[str,dict[str,float]]:
    """Aggregate downstream execution quality by durable planner decision identity."""
    path=state_root/"hands-off-grant-lifecycle.json"
    try:
        grants=json.loads(path.read_text(encoding="utf-8")).get("grants",{})
    except (OSError,json.JSONDecodeError,TypeError):
        return {}
    quality={}
    for record in grants.values():
        if not isinstance(record,dict):
            continue
        decision_id=record.get("planner_decision_id")
        if not isinstance(decision_id,str) or not decision_id:
            continue
        row=quality.setdefault(decision_id,{"samples":0.0,"failures":0.0,"repairs":0.0,
                                            "merged_verified":0.0,"product_reality_verified":0.0})
        row["samples"]+=1.0
        status=str(record.get("result_status") or "")
        failed=record.get("state") in {"ABANDONED","QUARANTINED"} or "FAILED" in status
        row["failures"]+=1.0 if failed else 0.0
        try:
            row["repairs"]+=max(float(record.get("repair_attempts",0)),0.0)
        except (TypeError,ValueError):
            pass
        outcome=record.get("outcome_quality")
        if outcome in {"MERGED_VERIFIED","PRODUCT_REALITY_VERIFIED"}:
            row["merged_verified"]+=1.0
        if outcome=="PRODUCT_REALITY_VERIFIED":
            row["product_reality_verified"]+=1.0
    return quality

def reconcile_dashboard_ci(state_root:Path,workspace:Path,run_fn=subprocess.run)->dict:
    """Observe exact durable PR heads and persist CI state without changing promotion authority."""
    try:
        lifecycle=json.loads((state_root/"hands-off-grant-lifecycle.json").read_text(encoding="utf-8")).get("grants",{})
    except (OSError,json.JSONDecodeError,TypeError):
        lifecycle={}
    rows=[]
    for gid,r in lifecycle.items():
        if not isinstance(r,dict) or not r.get("pr_number") or not r.get("head_sha") or not r.get("repository"):
            continue
        cmd=["gh","pr","checks",str(r["pr_number"]),"--repo",r["repository"],"--json","name,state,link"]
        p=run_fn(cmd,cwd=workspace,text=True,capture_output=True,check=False)
        checks=[]
        if p.returncode==0:
            try: checks=json.loads(p.stdout)
            except (json.JSONDecodeError,TypeError): checks=[]
        states={str(c.get("state","")).upper() for c in checks if isinstance(c,dict)}
        conclusion=("UNREADABLE" if p.returncode else "PENDING" if states & {"PENDING","QUEUED","IN_PROGRESS"}
                    else "FAILURE" if states & {"FAILURE","ERROR","CANCELLED","TIMED_OUT"}
                    else "SUCCESS" if checks and states <= {"SUCCESS","SKIPPED","NEUTRAL"} else "UNKNOWN")
        rows.append({"grant_id":gid,"repository":r["repository"],"pr_number":r["pr_number"],
                     "head_sha":r["head_sha"],"ci_conclusion":conclusion,"checks":checks})
    payload={"schema_version":1,"recorded_at":datetime.now(UTC).isoformat(),"pull_requests":rows}
    atomic_json(state_root/"dashboard-ci.json",payload)
    return payload

def write_dashboard_snapshot(state_root:Path)->dict:
    """Persist a read-only, phone-dashboard-friendly Foundry state projection."""
    def load_json(path:Path,default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError,json.JSONDecodeError,TypeError):
            return default
    lifecycle=load_json(state_root/"hands-off-grant-lifecycle.json",{}).get("grants",{})
    workers=load_json(state_root/"parallel-worker-pool.json",{})
    dispatch=load_json(state_root/"dispatch-decision.json",{})
    rotations=load_json(state_root/"async-gate-rotation-history.json",{})
    ci=load_json(state_root/"dashboard-ci.json",{})
    try:
        kpi_events=load_events(state_root/"kpi-events.jsonl")
        kpi_rolling=build_rolling_reports(
            kpi_events,
            end=datetime.now(UTC).isoformat(),
        )
        kpi_state={"status":"MEASURED" if kpi_events else "UNKNOWN",
                   "event_count":len(kpi_events),"rolling_reports":kpi_rolling}
    except (KpiTelemetryError,OSError,ValueError) as exc:
        kpi_state={"status":"FAILED","event_count":None,"reason":str(exc),"rolling_reports":{}}
    active=[]
    for gid,r in lifecycle.items():
        if not isinstance(r,dict) or r.get("state") not in {"ISSUED","CLAIMED","RUNNING","QUARANTINED","DEFERRED"}:
            continue
        projection=canonical_job_projection(r)
        active.append({"grant_id":gid,
                       "repository":r.get("repository") or (r.get("execution_slice") or {}).get("repository"),
                       "slice_id":r.get("slice_id"),"runner_id":r.get("runner_id"),
                       "state":r.get("state"),"job_state":projection["job_state"],
                       "planner_decision_id":r.get("planner_decision_id")})
    terminal=[]
    counters={"completed":0,"failed":0,"merged_verified":0,"product_reality_verified":0,"repairs":0}
    for gid,r in lifecycle.items():
        if not isinstance(r,dict) or r.get("state") not in {"COMPLETED","ABANDONED"}:
            continue
        status=str(r.get("result_status") or "")
        outcome=r.get("outcome_quality")
        counters["completed"]+=1 if r.get("state")=="COMPLETED" else 0
        counters["failed"]+=1 if r.get("state")=="ABANDONED" or "FAILED" in status else 0
        counters["merged_verified"]+=1 if outcome in {"MERGED_VERIFIED","PRODUCT_REALITY_VERIFIED"} else 0
        counters["product_reality_verified"]+=1 if outcome=="PRODUCT_REALITY_VERIFIED" else 0
        try: counters["repairs"]+=max(int(r.get("repair_attempts",0)),0)
        except (TypeError,ValueError): pass
        projection=canonical_job_projection(r)
        terminal.append({"grant_id":gid,"repository":r.get("repository") or (r.get("execution_slice") or {}).get("repository"),
                         "slice_id":r.get("slice_id"),"runner_id":r.get("runner_id"),"state":r.get("state"),
                         "job_state":projection["job_state"],
                         "result_status":status,"outcome_quality":outcome,"finished_at":r.get("finished_at"),
                         "pr_number":r.get("pr_number"),"pr_url":r.get("pr_url"),
                         "head_sha":r.get("head_sha"),"execution_stage":r.get("execution_stage")})
    terminal.sort(key=lambda row:row.get("finished_at") or "",reverse=True)
    elapsed_seconds=0.0
    for r in lifecycle.values():
        if not isinstance(r,dict): continue
        try:
            if r.get("execution_seconds") is not None:
                elapsed_seconds+=max(float(r["execution_seconds"]),0.0)
            else:
                started=datetime.fromisoformat(r.get("execution_started_at") or r.get("claimed_at") or r.get("issued_at"))
                finished=datetime.fromisoformat(r.get("execution_finished_at") or r.get("finished_at"))
                if started.tzinfo is None: started=started.replace(tzinfo=UTC)
                if finished.tzinfo is None: finished=finished.replace(tzinfo=UTC)
                elapsed_seconds+=max((finished-started).total_seconds(),0.0)
        except (TypeError,ValueError):
            pass
    verified=counters["merged_verified"]
    advancement_per_hour=(verified*3600.0/elapsed_seconds) if elapsed_seconds>0 else 0.0
    pool=workers.get("pool",{}) if isinstance(workers,dict) else {}
    pool_workers=pool.get("workers",[]) if isinstance(pool,dict) else []
    runtime_leases=(workers.get("runtime_leases",{}) or {}).get("leases",[]) if isinstance(workers,dict) else []
    leased_workers={str(row.get("worker_id") or "") for row in runtime_leases if isinstance(row,dict)}
    active_grant_workers={str(r.get("worker_id") or "") for r in lifecycle.values()
                          if isinstance(r,dict) and r.get("state") in {"ISSUED","CLAIMED","QUARANTINED"}}
    observed_pool=[]
    for w in pool_workers:
        if not isinstance(w,dict) or w.get("done",False):
            continue
        worker_id=str(w.get("worker_id") or "")
        row=dict(w)
        row["activity_status"]="RUNNING" if worker_id in leased_workers or worker_id in active_grant_workers else "STALE"
        observed_pool.append(row)
    running_pool=[w for w in observed_pool if w["activity_status"]=="RUNNING"]
    payload={"schema_version":1,"recorded_at":datetime.now(UTC).isoformat(),
             "active_work":active,"active_count":max(len(active),len(running_pool)),
             "active_worker_pool":observed_pool,
             "recent_completed_work":terminal[:20],"outcome_counters":counters,
             "flow":{"verified_advancement_per_worker_hour":advancement_per_hour,
                     "measured_worker_seconds":elapsed_seconds,
                     "kpi":kpi_state},
             "github":{"pull_requests":[row for row in terminal if row.get("pr_number") is not None],
                       "ci":ci.get("pull_requests",[]),
                       "promotion_gates":[{"repository":row.get("repository"),"pr_number":row.get("pr_number"),
                                           "head_sha":row.get("head_sha"),"stage":row.get("execution_stage"),
                                           "result_status":row.get("result_status")}
                                          for row in terminal if row.get("pr_number") is not None]},
             "queue":{"selected_repository":dispatch.get("selected_repository"),
                      "selected_product":dispatch.get("selected_product"),
                      "decisions":dispatch.get("decisions",[]),
                      "recent_async_rotations":rotations.get("rotations",[])[-20:]},
             "worker_pool":workers.get("pool",{}),
             "planner_decision_quality":planner_decision_quality(state_root),
             "products":{}}
    decisions=state_root/"planner-decisions"
    if decisions.exists():
        for path in sorted(decisions.glob("*.json")):
            data=load_json(path,{})
            payload["products"][path.stem]={"candidate_count":data.get("candidate_count",0),
                                            "selected_count":data.get("selected_count",0),
                                            "candidates":data.get("candidates",[])}
    atomic_json(state_root/"telemetry"/"foundry-dashboard.json",payload)
    return payload

def record_async_gate_rotation(state_root:Path,*,blocked_repository:str|None,blocked_status:str,
                               next_repository:str|None,burst_id:str|None,process_instance_id:str|None)->dict:
    """Persist proof that an asynchronous product gate caused portfolio rotation rather than idle."""
    path=state_root/"async-gate-rotation-history.json"
    try:
        history=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError,TypeError,ValueError):
        history={"schema_version":1,"rotations":[]}
    rotations=history.get("rotations") if isinstance(history,dict) else None
    if not isinstance(rotations,list):
        rotations=[]
    prior_count=history.get("rotation_count",len(rotations)) if isinstance(history,dict) else len(rotations)
    if not isinstance(prior_count,int) or isinstance(prior_count,bool) or prior_count < len(rotations):
        prior_count=len(rotations)
    record={"recorded_at_utc":datetime.now(UTC).isoformat(),
            "blocked_repository":blocked_repository,"blocked_status":blocked_status,
            "next_repository":next_repository,"burst_id":burst_id,
            "process_instance_id":process_instance_id,
            "rotated":bool(next_repository and next_repository!=blocked_repository)}
    rotations.append(record)
    payload={"schema_version":1,"rotation_count":prior_count+1,"rotations":rotations[-100:]}
    atomic_json(path,payload)
    return record

def record_burst_repository_pass(state_root:Path,*,repository:str|None,builder_status:str,
                                 burst_id:str,process_instance_id:str,sequence:int)->dict:
    """Persist burst-level proof of consecutive autonomous repository selections."""
    path=state_root/"autonomous-burst-proof.json"
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError,TypeError,ValueError):
        payload={}
    same_burst=isinstance(payload,dict) and payload.get("burst_id")==burst_id
    passes=payload.get("passes",[]) if same_burst else []
    if not isinstance(passes,list): passes=[]
    record={"sequence":sequence,"repository":repository,"builder_status":builder_status,
            "process_instance_id":process_instance_id,"recorded_at_utc":datetime.now(UTC).isoformat()}
    passes.append(record)
    repositories=sorted({p.get("repository") for p in passes if isinstance(p,dict) and isinstance(p.get("repository"),str) and p.get("repository")})
    proof={"schema_version":1,"burst_id":burst_id,"pass_count":len(passes),
           "distinct_repositories":repositories,"distinct_repository_count":len(repositories),
           "multi_repository_proven":len(repositories)>=2 and len(passes)>=2,
           "passes":passes[-100:]}
    atomic_json(path,proof)
    return proof

def record_product_identity_advancement(state_root:Path,*,repository:str|None,builder_status:str,lifecycle_advanced:bool,
                                        burst_id:str,process_instance_id:str,sequence:int)->dict:
    product=product_for_repository(repository)
    advanced=bool(product) and (builder_status in {"PR_OPEN","GREEN","MERGED","PRODUCT_REALITY_VERIFIED","REPAIRED"} or lifecycle_advanced)
    path=state_root/"portfolio-product-advancement-proof.json"
    try: payload=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError,TypeError,ValueError): payload={}
    events=payload.get("events",[]) if isinstance(payload,dict) and payload.get("burst_id")==burst_id else []
    if not isinstance(events,list): events=[]
    events.append({"sequence":sequence,"repository":repository,"product":product,"builder_status":builder_status,
                   "lifecycle_advanced":lifecycle_advanced,"advanced":advanced,"process_instance_id":process_instance_id,
                   "recorded_at_utc":datetime.now(UTC).isoformat()})
    products=sorted({e.get("product") for e in events if isinstance(e,dict) and e.get("advanced") is True and e.get("product") in {"knowledge-engine","rocksmith","everward","voicestudio"}})
    all_products_proven=len(products)==4
    proof={"schema_version":2,"burst_id":burst_id,"advanced_products":products,"advanced_product_count":len(products),
           "all_products_proven":all_products_proven,
           "all_three_products_proven":all_products_proven,
           "events":events[-100:]}
    atomic_json(path,proof); return proof

def selected_builder_repository(state_root:Path,expected_invocation_id:str|None)->str|None:
    """Return repository evidence only when bound to the current builder invocation."""
    if not expected_invocation_id:
        return None
    try:
        payload=json.loads((state_root/"builder.json").read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError,TypeError,ValueError):
        return None
    if not isinstance(payload,dict) or payload.get("invocation_id")!=expected_invocation_id:
        return None
    repository=payload.get("repository")
    return repository if repository in PRODUCT_BY_REPOSITORY else None

def maybe_record_async_rotation(state_root:Path,*,previous_repository:str|None,previous_status:str|None,
                                current_repository:str|None,burst_id:str|None,process_instance_id:str|None)->dict|None:
    async_states={"REVIEW_BLOCKED","CI_PENDING","PR_BLOCKED"}
    if previous_status not in async_states or not previous_repository or not current_repository:
        return None
    return record_async_gate_rotation(state_root,blocked_repository=previous_repository,
        blocked_status=previous_status,next_repository=current_repository,
        burst_id=burst_id,process_instance_id=process_instance_id)

def continuation_delay(*,lifecycle_advanced:bool,liveness_reacquire:dict,builder_ok:bool,builder_status:str,normal_seconds:int)->int:
    """Choose the next supervisor pass without turning successful work into idle time."""
    if lifecycle_advanced:
        return 1
    productive={"PR_OPEN","REVIEW_BLOCKED","CI_PENDING","PR_BLOCKED"}
    terminal_nonproductive={"NO_WORK","NO_OP","SELF_REPAIRED_TO_NO_OP","AUTONOMOUS_CHANGE_REJECTED",
                            "COMMIT_FAILED","NO_ELIGIBLE_REPOSITORY"}
    if builder_ok and builder_status in productive:
        return 1
    # USER_ACTIVE is transient, not terminal. A persistent supervisor must retry
    # after a bounded delay so the idle transition can acquire work without an
    # external continuation signal. Keep the delay long enough to avoid a hot loop.
    if liveness_reacquire.get("requested") and builder_status=="USER_ACTIVE":
        return max(60,min(normal_seconds,300))
    if liveness_reacquire.get("requested") and builder_status not in terminal_nonproductive:
        return 1
    return max(60,normal_seconds)

def reconcile_continuation_journal(state_root:Path)->dict:
    """Reconcile interrupted continuation commits before advancing sequence authority."""
    journal_path=state_root/"continuation-pass-journal.json"
    history_path=state_root/"continuation-pass-history.json"
    if not journal_path.exists():
        return {"status":"NO_JOURNAL"}
    try:
        journal=json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError,TypeError,ValueError) as exc:
        raise RuntimeError("unreadable or malformed continuation journal") from exc
    if not isinstance(journal,dict) or journal.get("schema_version")!=1:
        raise RuntimeError("invalid continuation journal")
    status=journal.get("status")
    if status not in {"PREPARED","COMMITTED","ABORTED"}:
        raise RuntimeError("invalid continuation journal status")
    if status=="ABORTED":
        return {"status":"ABORTED"}
    try:
        history=json.loads(history_path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError,TypeError,ValueError):
        history=None
    history_valid=False
    if isinstance(history,dict) and history.get("schema_version")==4:
        candidate=history.get("passes")
        total=history.get("pass_count")
        immediate_total=history.get("immediate_continuation_count")
        anchor_material={"pass_count":total,"immediate_continuation_count":immediate_total,
                         "retained_predecessor_hash":history.get("retained_predecessor_hash"),
                         "chain_head":history.get("chain_head")}
        expected_anchor=hashlib.sha256(json.dumps(anchor_material,sort_keys=True,separators=(",",":")).encode()).hexdigest()
        history_valid=(isinstance(candidate,list)
                       and isinstance(total,int) and not isinstance(total,bool)
                       and isinstance(immediate_total,int) and not isinstance(immediate_total,bool)
                       and 0 <= immediate_total <= total <= 10000000
                       and history.get("anchor_hash")==expected_anchor)
        expected=history.get("retained_predecessor_hash")
        if history_valid:
            for item in candidate:
                if not isinstance(item,dict) or item.get("previous_hash")!=expected:
                    history_valid=False; break
                material={k:v for k,v in item.items() if k!="record_hash"}
                calculated=hashlib.sha256(json.dumps(material,sort_keys=True,separators=(",",":")).encode()).hexdigest()
                if item.get("record_hash")!=calculated:
                    history_valid=False; break
                expected=item.get("record_hash")
            if history_valid:
                history_valid=(expected==history.get("chain_head") and (not candidate or candidate[-1].get("sequence")==total))
    matches=(history_valid
             and history.get("pass_count")==journal.get("sequence")
             and history.get("chain_head")==journal.get("record_hash")
             and history.get("anchor_hash")==journal.get("anchor_hash"))
    if status=="PREPARED":
        if matches:
            committed=dict(journal)
            committed["status"]="COMMITTED"
            committed["reconciled_at_utc"]=datetime.now(UTC).isoformat()
            atomic_json(journal_path,committed)
            return {"status":"RECOVERED_COMMITTED","sequence":journal.get("sequence")}
        aborted=dict(journal)
        aborted["status"]="ABORTED"
        aborted["aborted_at_utc"]=datetime.now(UTC).isoformat()
        atomic_json(journal_path,aborted)
        return {"status":"RECOVERED_ABORTED","sequence":journal.get("sequence")}
    if not matches:
        raise RuntimeError("committed continuation journal diverges from authoritative or invalid history")
    return {"status":"COMMITTED","sequence":journal.get("sequence")}

def record_continuation_pass(state_root:Path,*,builder_status:str,delay_seconds:int,lifecycle_advanced:bool,liveness_reacquire:dict,burst_id:str|None=None,process_instance_id:str|None=None)->dict:
    """Persist validated monotonic evidence that one wake produced another autonomous pass."""
    reconcile_continuation_journal(state_root)
    path=state_root/"continuation-pass-history.json"
    passes=[]; total=0; immediate_total=0; chain_head=None
    try:
        history=json.loads(path.read_text(encoding="utf-8"))
        candidate=history.get("passes",[]) if isinstance(history,dict) else []
        candidate_total=history.get("pass_count") if isinstance(history,dict) else None
        candidate_immediate=history.get("immediate_continuation_count") if isinstance(history,dict) else None
        valid=(history.get("schema_version") in {1,2,3,4}
               and isinstance(candidate,list)
               and isinstance(candidate_total,int) and not isinstance(candidate_total,bool)
               and 0 <= candidate_total <= 10000000
               and isinstance(candidate_immediate,int) and not isinstance(candidate_immediate,bool)
               and 0 <= candidate_immediate <= candidate_total)
        if valid and history.get("schema_version")==2 and candidate:
            tail=candidate[-1]
            valid=(isinstance(tail,dict) and tail.get("sequence")==candidate_total)
        if valid and history.get("schema_version")==4:
            anchor_material={"pass_count":candidate_total,
                             "immediate_continuation_count":candidate_immediate,
                             "retained_predecessor_hash":history.get("retained_predecessor_hash"),
                             "chain_head":history.get("chain_head")}
            anchor_hash=hashlib.sha256(json.dumps(anchor_material,sort_keys=True,separators=(",",":")).encode()).hexdigest()
            valid=(history.get("anchor_hash")==anchor_hash)
            expected=history.get("retained_predecessor_hash")
            for item in candidate:
                if not isinstance(item,dict):
                    valid=False; break
                stored=item.get("record_hash")
                material={k:v for k,v in item.items() if k!="record_hash"}
                if material.get("previous_hash")!=expected:
                    valid=False; break
                calculated=hashlib.sha256(json.dumps(material,sort_keys=True,separators=(",",":")).encode()).hexdigest()
                if stored!=calculated:
                    valid=False; break
                expected=stored
            if valid:
                valid=(expected==history.get("chain_head") and (not candidate or candidate[-1].get("sequence")==candidate_total))
                chain_head=expected
        if valid:
            passes=candidate
            total=candidate_total
            immediate_total=candidate_immediate
            if history.get("schema_version") not in {3,4}:
                chain_head=None
    except (OSError,json.JSONDecodeError,TypeError,ValueError,AttributeError):
        pass
    immediate=delay_seconds==1
    sequence=total+1
    record={"sequence":sequence,"recorded_at_utc":datetime.now(UTC).isoformat(),
            "previous_hash":chain_head,
            "burst_id":burst_id,"process_instance_id":process_instance_id,"builder_status":builder_status,"delay_seconds":delay_seconds,
            "immediate_continuation":immediate,"lifecycle_advanced":lifecycle_advanced,
            "liveness_reacquire_requested":bool(liveness_reacquire.get("requested"))}
    material=json.dumps(record,sort_keys=True,separators=(",",":")).encode()
    record["record_hash"]=hashlib.sha256(material).hexdigest()
    passes.append(record)
    retained=passes[-100:]
    retained_predecessor_hash=retained[0].get("previous_hash") if retained else None
    payload={"schema_version":4,"pass_count":sequence,
             "immediate_continuation_count":immediate_total+(1 if immediate else 0),
             "chain_head":record["record_hash"],
             "retained_predecessor_hash":retained_predecessor_hash,"passes":retained}
    anchor_material={k:payload[k] for k in ("pass_count","immediate_continuation_count","retained_predecessor_hash","chain_head")}
    payload["anchor_hash"]=hashlib.sha256(json.dumps(anchor_material,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    journal=state_root/"continuation-pass-journal.json"
    atomic_json(journal,{"schema_version":1,"status":"PREPARED","sequence":sequence,
                    "record_hash":record["record_hash"],"anchor_hash":payload["anchor_hash"],
                    "prepared_at_utc":datetime.now(UTC).isoformat()})
    atomic_json(path,payload)
    atomic_json(journal,{"schema_version":1,"status":"COMMITTED","sequence":sequence,
                    "record_hash":record["record_hash"],"anchor_hash":payload["anchor_hash"],
                    "committed_at_utc":datetime.now(UTC).isoformat()})
    return record
