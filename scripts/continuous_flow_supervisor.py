"""Current-user continuous engineering supervisor; no admin Task Scheduler required."""
from __future__ import annotations
import argparse, atexit, hashlib, json, os, shutil, socket, subprocess, sys, time, uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
try:
    from scripts.autonomous_slice_builder import _acquire_lifecycle_lock, _release_lifecycle_lock
except ModuleNotFoundError:
    from autonomous_slice_builder import _acquire_lifecycle_lock, _release_lifecycle_lock

from scripts.capability_slice_contract import derive_slice, derive_slices
from scripts.product_completion_proof import build_completion_proof
from scripts.windows_session_handoff import active_interactive_sessions, boot_owner_should_yield
from scripts.agent_voice import manager_status, worker_status
from scripts.blocker_buddy import diagnose as diagnose_blocker
from project_orchestrator.decision_outcome import attribute as attribute_decision_outcome
from project_orchestrator.decision_learning import compile_decision_learning, planning_hint
from project_orchestrator.jev_evidence import load_jsonl
from project_orchestrator.parallel_worker_pool import ParallelWorkerPool
from project_orchestrator.runner_registry import DEFAULT_RUNNERS, RunnerRegistry
from project_orchestrator.execution_runtime import require_runtime_capabilities
from project_orchestrator.runtime_leases import RuntimeLeasePool, DEFAULT_RUNTIME_CAPACITIES

RUNTIME_LEASES=RuntimeLeasePool(DEFAULT_RUNTIME_CAPACITIES)

def atomic(path:Path,payload:dict)->None:
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(f"{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
    try:
        tmp.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
        for attempt in range(5):
            try:
                tmp.replace(path)
                break
            except PermissionError:
                if attempt == 4:
                    # Windows scanners/readers may deny rename-over-open-file while
                    # still allowing a normal overwrite. Preserve the temp-first
                    # write and degrade to copy-overwrite rather than killing flow.
                    shutil.copyfile(tmp,path)
                    break
                time.sleep(0.1 * (attempt + 1))
    finally:
        tmp.unlink(missing_ok=True)

def start_merge_watcher(repo_root:Path,state_root:Path,poll_seconds:int)->subprocess.Popen:
    return subprocess.Popen([sys.executable,str(repo_root/"scripts"/"green_pr_watch_loop.py"),
        "--state",str(state_root/"green-pr-watcher.json"),"--poll-seconds",str(poll_seconds)],
        cwd=repo_root,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

def run_linear_reconciliation(repo_root:Path,state_root:Path)->int|None:
    bindings=state_root/"linear-bindings.json"
    if not bindings.exists():
        return None
    p=subprocess.run([sys.executable,str(repo_root/"scripts"/"linear_reconciliation_worker.py"),
        "--bindings",str(bindings),"--output",str(state_root/"linear-mutations.json")],
        cwd=repo_root,text=True,capture_output=True,check=False)
    (state_root/"linear-last-output.txt").write_text((p.stdout or "")+(p.stderr or ""),encoding="utf-8")
    return p.returncode

def builder_health(state_root:Path,returncode:int,expected_invocation_id:str|None=None)->tuple[bool,str]:
    path=state_root/"builder.json"
    if returncode != 0:
        return False,"PROCESS_FAILURE"
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
        status=payload.get("status","UNKNOWN")
    except (OSError,json.JSONDecodeError):
        return False,"STATE_UNREADABLE"
    if expected_invocation_id is not None and payload.get("invocation_id")!=expected_invocation_id:
        return False,"STALE_BUILDER_STATE"
    degraded={"P1_BUILDER_FAILURE","BUILDER_FAILED","PREFLIGHT_FAILED","PUSH_FAILED","PR_CREATE_FAILED"}
    productive={"USER_ACTIVE","IMPLEMENTING","PREFLIGHT_GREEN","PR_OPEN","CI_PENDING","GREEN","MERGED","PRODUCT_REALITY_PENDING","PRODUCT_REALITY_VERIFIED"}
    return status in productive and status not in degraded,status

def observe_live_heads(repo_root:Path,workspace_root:Path,state_root:Path)->tuple[bool,str]:
    """Snapshot contract-declared workspace identity and HEAD independently of readiness certificates."""
    try:
        contract=json.loads((repo_root/"engineering"/"hands-off-evolution.json").read_text(encoding="utf-8"))
        repositories=sorted({repo for repos in contract.get("products",{}).values() for repo in repos})
    except (OSError,json.JSONDecodeError,TypeError):
        return False,"LIVE_HEAD_CONTRACT_UNREADABLE"
    observed={}
    for repo in repositories:
        ws=workspace_root/repo.split("/",1)[-1]
        if not ws.is_dir():
            continue
        origin=subprocess.run(["git","remote","get-url","origin"],cwd=ws,text=True,capture_output=True,check=False)
        head=subprocess.run(["git","rev-parse","HEAD"],cwd=ws,text=True,capture_output=True,check=False)
        if origin.returncode or head.returncode or repo.lower() not in origin.stdout.lower():
            continue
        value=head.stdout.strip()
        if value:
            observed[repo]=value
    atomic(state_root/"hands-off-live-heads.json",observed)
    return bool(observed),"LIVE_HEADS_OBSERVED" if observed else "LIVE_HEADS_UNAVAILABLE"

def evolution_authorized(repo_root:Path,workspace_root:Path,state_root:Path)->tuple[bool,str,list[str],list[str]]:
    activation=state_root/"hands-off-activation.json"
    observed=state_root/"hands-off-live-heads.json"
    live_ok,_=observe_live_heads(repo_root,workspace_root,state_root)
    if not activation.exists() or not live_ok or not observed.exists():
        return False,"ACTIVATION_EVIDENCE_MISSING",[],[]
    p=subprocess.run([sys.executable,str(repo_root/"scripts"/"evolution_activation_gate.py"),
        "--activation-state",str(activation),"--observed-heads",str(observed),
        "--contract",str(repo_root/"engineering"/"hands-off-evolution.json")],
        cwd=repo_root,text=True,capture_output=True,check=False)
    (state_root/"activation-last-output.txt").write_text((p.stdout or "")+(p.stderr or ""),encoding="utf-8")
    try:
        payload=json.loads(p.stdout)
        reason=payload.get("reason","ACTIVATION_UNKNOWN")
        results=payload.get("products",{})
        products=sorted(name for name,result in results.items()
                        if isinstance(result,dict) and result.get("allowed") is True)
        stale=sorted(name for name,result in results.items()
                     if isinstance(result,dict) and result.get("reason")=="PRODUCT_HEADS_STALE")
    except (json.JSONDecodeError,TypeError):
        reason="ACTIVATION_UNREADABLE"
        products=[]
        stale=[]
    return p.returncode==0 and bool(products),reason,products,stale

def refresh_readiness(repo_root:Path,workspace_root:Path,state_root:Path)->tuple[bool,str]:
    """Regenerate exact-head activation evidence without conversational intervention."""
    repos=[
        workspace_root/"knowledge-engine-core", workspace_root/"knowledge-engine-web",
        workspace_root/"knowledge-engine-ai", workspace_root/"rocksmith-cdlc-generator",
        workspace_root/"Project-Everward",
    ]
    if not all(path.is_dir() for path in repos):
        return False,"READINESS_WORKSPACE_INCOMPLETE"
    activation=state_root/"hands-off-activation.json"
    report=state_root/"hands-off-readiness.json"
    cmd=[sys.executable,str(repo_root/"scripts"/"hands_off_readiness.py"),
         *[str(path) for path in repos],"--output",str(report),"--activation-state",str(activation)]
    p=subprocess.run(cmd,cwd=repo_root,text=True,capture_output=True,check=False)
    (state_root/"readiness-last-output.txt").write_text((p.stdout or "")+(p.stderr or ""),encoding="utf-8")
    if p.returncode!=0:
        return False,"READINESS_NOT_GREEN"
    try:
        payload=json.loads(activation.read_text(encoding="utf-8"))
        heads={
            repo: head
            for product in payload.get("products",{}).values()
            if isinstance(product,dict) and product.get("enabled") is True
            for repo,head in product.get("repositories",{}).items()
            if head
        }
    except (OSError,json.JSONDecodeError):
        return False,"READINESS_ACTIVATION_UNREADABLE"
    atomic(state_root/"hands-off-expected-heads.json",heads)
    return True,"READINESS_REFRESHED"

def recertify_product(repo_root:Path,workspace_root:Path,state_root:Path,product:str)->tuple[bool,str]:
    """Audit one stale product and merge only its new authority."""
    contract_path=repo_root/"engineering"/"hands-off-evolution.json"
    try:
        contract=json.loads(contract_path.read_text(encoding="utf-8"))
        declared=contract["products"][product]
        if not isinstance(declared,list) or not declared:
            raise TypeError
    except (OSError,json.JSONDecodeError,KeyError,TypeError):
        return False,"RECERTIFICATION_CONTRACT_UNREADABLE"
    repos=[workspace_root/repo.split("/",1)[-1] for repo in declared]
    partial=state_root/f"hands-off-recertify-{product}.json"
    report=state_root/f"hands-off-readiness-{product}.json"
    cmd=[sys.executable,str(repo_root/"scripts"/"hands_off_readiness.py"),
         *[str(path) for path in repos],"--product",product,
         "--output",str(report),"--activation-state",str(partial)]
    p=subprocess.run(cmd,cwd=repo_root,text=True,capture_output=True,check=False)
    (state_root/f"recertify-{product}-last-output.txt").write_text((p.stdout or "")+(p.stderr or ""),encoding="utf-8")
    try:
        payload=json.loads(partial.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError):
        return False,"RECERTIFICATION_UNREADABLE"
    return merge_product_recertification(state_root,product,payload)

def merge_product_recertification(state_root:Path,product:str,partial:dict)->tuple[bool,str]:
    """Atomically replace one product's authority while preserving unrelated products."""
    path=state_root/"hands-off-activation.json"
    try:
        current=json.loads(path.read_text(encoding="utf-8"))
        candidate=partial["products"][product]
    except (OSError,json.JSONDecodeError,KeyError,TypeError):
        return False,"RECERTIFICATION_UNREADABLE"
    products=dict(current.get("products",{}))
    products[product]=candidate
    enabled=any(isinstance(v,dict) and v.get("enabled") is True for v in products.values())
    current.update({"schema_version":2,"products":products,"enabled":enabled,
                    "reason":"READINESS_PASS" if enabled else "READINESS_NOT_PASS"})
    current["heads"]={repo:head for value in products.values()
                      if isinstance(value,dict) and value.get("enabled") is True
                      for repo,head in value.get("repositories",{}).items() if head}
    atomic(path,current)
    atomic(state_root/"hands-off-expected-heads.json",current["heads"])
    return candidate.get("enabled") is True,"PRODUCT_RECERTIFIED" if candidate.get("enabled") is True else "PRODUCT_QUARANTINED"

def request_immediate_builder(state_root:Path)->None:
    """Record that PR lifecycle progress should immediately feed the next build cycle."""
    atomic(state_root/"continuation-request.json",{
        "requested_at_utc":datetime.now(UTC).isoformat(),
        "reason":"PR_LIFECYCLE_ADVANCED",
    })

def reconcile_merge_feedback(state_root:Path,merged:list[dict])->list[dict]:
    """Advance exact merged slices into capability state before selecting more work."""
    outcomes=[]
    for event in merged:
        slice_result=reconcile_verified_merge(state_root,event)
        outcome={"event":event,"slice":slice_result}
        capability_id=slice_result.get("capability_id")
        if capability_id:
            outcome["capability"]=reconcile_capability_from_slice_evidence(
                state_root,event.get("repository"),capability_id)
        outcomes.append(outcome)
    return outcomes

def merge_watcher_advanced(state_root:Path)->bool:
    """Consume each successful merge observation exactly once, after product-state reconciliation."""
    path=state_root/"green-pr-watcher.json"
    cursor=state_root/"green-pr-watcher-consumed.json"
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError):
        return False
    merged=[item for item in payload.get("results",[]) if item.get("merged")]
    if not merged:
        return False
    # A watcher timestamp is observation metadata, not merge identity. Stable
    # repo/PR/head identity prevents the same merge from repeatedly re-triggering
    # autonomous selection on every watcher poll.
    event={"merged":sorted([
        {"repository":item.get("repository"),"pr":item.get("pr"),"head":item.get("head")} for item in merged
    ],key=lambda item:(str(item.get("repository")),str(item.get("pr")),str(item.get("head"))))}
    try:
        previous=json.loads(cursor.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError):
        previous=None
    if previous==event:
        return False
    outcomes=reconcile_merge_feedback(state_root,event["merged"])
    blocking={"MERGE_IDENTITY_INCOMPLETE","LIFECYCLE_UNAVAILABLE","MERGE_SLICE_IDENTITY_UNPROVEN",
              "COMPLETION_EVIDENCE_CONFLICT","CAPABILITY_GRAPH_UNAVAILABLE","CAPABILITY_IDENTITY_UNPROVEN",
              "CAPABILITY_SLICES_REQUIRED","CAPABILITY_SLICE_IDENTITY_UNPROVEN","SLICE_EVIDENCE_UNREADABLE",
              "SLICE_EVIDENCE_IDENTITY_CONFLICT","CAPABILITY_COMPLETION_CONFLICT"}
    if any(item.get("slice",{}).get("status") in blocking
           or item.get("capability",{}).get("status") in blocking for item in outcomes):
        atomic(state_root/"merge-feedback-blocked.json",{"event":event,"outcomes":outcomes})
        return False
    blocked=state_root/"merge-feedback-blocked.json"
    if blocked.exists(): blocked.unlink()
    atomic(state_root/"merge-feedback-last.json",{"event":event,"outcomes":outcomes})
    atomic(cursor,event)
    return True

def reconcile_verified_merge(state_root:Path,event:dict)->dict:
    """Bind one exact merged PR/head to its durable execution slice and emit evidence once."""
    repo=event.get("repository"); pr=event.get("pr"); head=event.get("head")
    if not repo or not isinstance(pr,int) or not head:
        return {"status":"MERGE_IDENTITY_INCOMPLETE"}
    lifecycle=state_root/"hands-off-grant-lifecycle.json"
    try:
        grants=json.loads(lifecycle.read_text(encoding="utf-8")).get("grants",{})
    except (OSError,json.JSONDecodeError,TypeError):
        return {"status":"LIFECYCLE_UNAVAILABLE"}
    matches=[(gid,r) for gid,r in grants.items() if isinstance(r,dict)
             and r.get("repository")==repo and r.get("pr_number")==pr and r.get("head_sha")==head]
    if len(matches)!=1:
        return {"status":"MERGE_SLICE_IDENTITY_UNPROVEN","matches":len(matches)}
    grant_id,record=matches[0]
    capability_id=record.get("capability_id"); slice_id=record.get("slice_id")
    if not capability_id or not slice_id:
        return {"status":"MERGE_SLICE_IDENTITY_UNPROVEN","matches":1}
    evidence={"schema_version":1,"status":"VERIFIED_COMPLETE","repository":repo,"pr_number":pr,
              "head_sha":head,"grant_id":grant_id,"capability_id":capability_id,"slice_id":slice_id,
              "acceptance":record.get("execution_slice",{}).get("acceptance",[])}
    evidence_dir=state_root/"slice-completion-evidence"; evidence_dir.mkdir(parents=True,exist_ok=True)
    path=evidence_dir/f"{repo.replace('/','__')}__{slice_id}.json"
    if path.exists():
        try:
            prior=json.loads(path.read_text(encoding="utf-8"))
        except (OSError,json.JSONDecodeError):
            return {"status":"COMPLETION_EVIDENCE_CONFLICT"}
        if prior==evidence:
            return {"status":"ALREADY_RECONCILED","evidence":str(path)}
        return {"status":"COMPLETION_EVIDENCE_CONFLICT"}
    atomic(path,evidence)
    return {"status":"SLICE_VERIFIED_COMPLETE","evidence":str(path),"capability_id":capability_id,"slice_id":slice_id}

def reconcile_capability_from_slice_evidence(state_root:Path,repo:str,capability_id:str)->dict:
    """Advance authoritative slice/capability state only from exact durable merge evidence."""
    graph_path=state_root/"capability-graphs"/f"{repo.replace('/','__')}.json"
    try:
        graph=json.loads(graph_path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError):
        return {"status":"CAPABILITY_GRAPH_UNAVAILABLE"}
    capability=next((c for c in graph.get("capabilities",[]) if isinstance(c,dict) and c.get("id")==capability_id),None)
    if capability is None:
        return {"status":"CAPABILITY_IDENTITY_UNPROVEN"}
    slices=capability.get("slices")
    if not isinstance(slices,list) or not slices:
        return {"status":"CAPABILITY_SLICES_REQUIRED"}
    evidence_dir=state_root/"slice-completion-evidence"; changed=False
    for slice_record in slices:
        if not isinstance(slice_record,dict) or not slice_record.get("id"):
            return {"status":"CAPABILITY_SLICE_IDENTITY_UNPROVEN"}
        sid=slice_record["id"]; path=evidence_dir/f"{repo.replace('/','__')}__{sid}.json"
        if not path.exists():
            continue
        try:
            evidence=json.loads(path.read_text(encoding="utf-8"))
        except (OSError,json.JSONDecodeError):
            return {"status":"SLICE_EVIDENCE_UNREADABLE","slice_id":sid}
        if (evidence.get("status")!="VERIFIED_COMPLETE" or evidence.get("repository")!=repo
                or evidence.get("capability_id")!=capability_id or evidence.get("slice_id")!=sid
                or not evidence.get("head_sha") or not isinstance(evidence.get("pr_number"),int)):
            return {"status":"SLICE_EVIDENCE_IDENTITY_CONFLICT","slice_id":sid}
        verification={"source":"verified_merge","pr_number":evidence["pr_number"],"head_sha":evidence["head_sha"],
                      "grant_id":evidence.get("grant_id")}
        if slice_record.get("state")!="VERIFIED_COMPLETE" or slice_record.get("verification")!=verification:
            slice_record["state"]="VERIFIED_COMPLETE"; slice_record["verification"]=verification; changed=True
    complete=all(isinstance(s,dict) and s.get("state")=="VERIFIED_COMPLETE" and s.get("verification") for s in slices)
    if complete:
        verification={"source":"all_required_slices_verified","slice_ids":[s["id"] for s in slices]}
        if capability.get("state")!="VERIFIED_COMPLETE" or capability.get("verification")!=verification:
            capability["state"]="VERIFIED_COMPLETE"; capability["verification"]=verification; changed=True
    elif capability.get("state")=="VERIFIED_COMPLETE":
        return {"status":"CAPABILITY_COMPLETION_CONFLICT"}
    if changed:
        atomic(graph_path,graph)
    return {"status":"CAPABILITY_VERIFIED_COMPLETE" if complete else "CAPABILITY_IN_PROGRESS",
            "capability_id":capability_id,"changed":changed}

def reconcile_product_completion(state_root:Path,repo:str,exact_head:str,payload:dict)->dict:
    """Promote exact-head final evidence into authoritative product completion."""
    graph=derive_slice(payload,repository=repo,exact_head=exact_head)
    if graph.get("status")!="DIRECTION_REQUIRED" or graph.get("issues")!=[{"code":"PRODUCT_COMPLETION_EVIDENCE_REQUIRED"}]:
        return {"status":"NOT_APPLICABLE"}
    root=state_root/"product-completion-evidence"
    def load(name:str):
        path=root/f"{repo.replace('/','__')}__{name}.json"
        if not path.exists(): return []
        try:
            value=json.loads(path.read_text(encoding="utf-8"))
        except (OSError,json.JSONDecodeError):
            return None
        return value if isinstance(value,list) else None
    acceptance=load("acceptance"); reality=load("product-reality")
    if acceptance is None or reality is None:
        return {"status":"EVIDENCE_UNREADABLE"}
    proof=build_completion_proof(exact_head=exact_head,acceptance=acceptance,product_reality=reality)
    if proof.get("status")!="VERIFIED":
        return proof
    payload["product_completion"]=proof["product_completion"]
    graph_path=state_root/"capability-graphs"/f"{repo.replace('/','__')}.json"
    atomic(graph_path,payload)
    return {"status":"PRODUCT_COMPLETION_VERIFIED","product_completion":proof["product_completion"]}

NO_OP_EXHAUSTION_THRESHOLD=2
NO_OP_TERMINAL_STATUSES={"NO_OP","SELF_REPAIRED_TO_NO_OP"}

def execution_slice_authority_digest(execution_slice:dict)->str:
    """Fingerprint bounded mutation authority so materially changed direction gets a clean retry budget."""
    material={key:execution_slice.get(key) for key in (
        "capability_id","slice_id","repository","objective","acceptance","allowed_paths","forbidden_paths"
    )}
    return hashlib.sha256(json.dumps(material,sort_keys=True,separators=(",",":")).encode()).hexdigest()

def repeated_noop_slice_evidence(state_root:Path,repo:str,execution_slice:dict,
                                 threshold:int=NO_OP_EXHAUSTION_THRESHOLD)->dict:
    """Detect repeated terminal no-op results for exactly the same bounded authority.

    Two consecutive no-op outcomes mean the current graph is no longer producing
    executable engineering. Fail closed and require a re-plan instead of
    redispatching the same stale slice forever. A materially changed slice
    authority has a different digest and therefore receives a fresh attempt.
    """
    digest=execution_slice_authority_digest(execution_slice)
    lifecycle=state_root/"hands-off-grant-lifecycle.json"
    try:
        grants=json.loads(lifecycle.read_text(encoding="utf-8")).get("grants",{})
    except (OSError,json.JSONDecodeError,TypeError):
        grants={}
    history=[]
    for grant_id,record in grants.items():
        if not isinstance(record,dict):
            continue
        authority=record.get("execution_slice")
        if not isinstance(authority,dict):
            continue
        if authority.get("repository")!=repo:
            continue
        if execution_slice_authority_digest(authority)!=digest:
            continue
        history.append({
            "grant_id":grant_id,
            "issued_at":record.get("issued_at") or "",
            "finished_at":record.get("finished_at") or "",
            "state":record.get("state"),
            "result_status":record.get("result_status"),
        })
    history.sort(key=lambda item:(item["issued_at"],item["finished_at"],item["grant_id"]))
    recent=history[-threshold:] if threshold>0 else []
    exhausted=(len(recent)>=threshold and all(
        item.get("state")=="COMPLETED" and item.get("result_status") in NO_OP_TERMINAL_STATUSES
        for item in recent
    ))
    return {
        "exhausted":exhausted,
        "threshold":threshold,
        "authority_digest":digest,
        "attempt_count":len(history),
        "recent_attempts":recent,
    }

def record_exhausted_slice_direction(state_root:Path,repo:str,exact_head:str,
                                     execution_slice:dict,evidence:dict)->None:
    """Persist machine-actionable re-plan evidence without converting no-op into completion."""
    issue={
        "code":"REPEATED_NO_OP_SLICE_EXHAUSTED",
        "capability_id":execution_slice.get("capability_id"),
        "slice_id":execution_slice.get("slice_id"),
        "authority_digest":evidence.get("authority_digest"),
        "threshold":evidence.get("threshold"),
        "recent_attempts":evidence.get("recent_attempts",[]),
        "next_action":"RECOMPILE_CAPABILITY_GRAPH_FROM_CURRENT_REPOSITORY_AUTHORITY",
    }
    request={
        "schema_version":1,
        "status":"DIRECTION_REQUIRED",
        "repository":repo,
        "exact_head":exact_head,
        "reason":"REPEATED_NO_OP_SLICE_EXHAUSTED",
        "issues":[issue],
        "requested_at_utc":datetime.now(UTC).isoformat(),
    }
    direction_dir=state_root/"direction-requests"
    direction_dir.mkdir(parents=True,exist_ok=True)
    atomic(direction_dir/f"{repo.replace('/','__')}.json",request)
    exhausted_dir=state_root/"slice-exhaustion-evidence"
    exhausted_dir.mkdir(parents=True,exist_ok=True)
    atomic(exhausted_dir/f"{repo.replace('/','__')}__{execution_slice.get('slice_id','unknown')}.json",{
        "schema_version":1,
        "repository":repo,
        "exact_head":exact_head,
        "capability_id":execution_slice.get("capability_id"),
        "slice_id":execution_slice.get("slice_id"),
        **evidence,
        "recorded_at_utc":datetime.now(UTC).isoformat(),
    })

def load_execution_slice(state_root:Path,repo:str,exact_head:str)->dict:
    """Derive bounded mutation authority from the repository's machine-readable capability graph."""
    path=state_root/"capability-graphs"/f"{repo.replace('/','__')}.json"
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as exc:
        direction_dir=state_root/"direction-requests"
        direction_dir.mkdir(parents=True,exist_ok=True)
        atomic(direction_dir/f"{repo.replace('/','__')}.json",{
            "schema_version":1,"status":"DIRECTION_REQUIRED","repository":repo,"exact_head":exact_head,
            "reason":"CAPABILITY_GRAPH_REQUIRED",
            "issues":[{"code":"CAPABILITY_GRAPH_REQUIRED","detail":type(exc).__name__}],
            "requested_at_utc":datetime.now(UTC).isoformat(),
        })
        raise RuntimeError(f"CAPABILITY_GRAPH_REQUIRED:{repo}")
    derived=derive_slice(payload,repository=repo,exact_head=exact_head)
    if derived.get("status")=="DIRECTION_REQUIRED" and derived.get("issues")==[{"code":"PRODUCT_COMPLETION_EVIDENCE_REQUIRED"}]:
        reconciliation=reconcile_product_completion(state_root,repo,exact_head,payload)
        if reconciliation.get("status")=="PRODUCT_COMPLETION_VERIFIED":
            derived=derive_slice(payload,repository=repo,exact_head=exact_head)
    request_path=state_root/"direction-requests"/f"{repo.replace('/','__')}.json"
    if derived.get("status")=="NO_WORK":
        request_path.unlink(missing_ok=True)
        return {"status":"NO_WORK","repository":repo,"exact_head":exact_head}
    if derived.get("status")!="READY" or not isinstance(derived.get("slice"),dict):
        request={
            "schema_version":1,
            "status":"DIRECTION_REQUIRED",
            "repository":repo,
            "exact_head":exact_head,
            "reason":derived.get("status","UNKNOWN"),
            "issues":derived.get("issues",[]),
            "requested_at_utc":datetime.now(UTC).isoformat(),
        }
        direction_dir=state_root/"direction-requests"
        direction_dir.mkdir(parents=True,exist_ok=True)
        atomic(direction_dir/f"{repo.replace('/','__')}.json",request)
        raise RuntimeError(f"EXECUTION_SLICE_NOT_READY:{repo}:{derived.get('status','UNKNOWN')}")
    execution_slice=derived["slice"]
    exhaustion=repeated_noop_slice_evidence(state_root,repo,execution_slice)
    if exhaustion["exhausted"]:
        record_exhausted_slice_direction(state_root,repo,exact_head,execution_slice,exhaustion)
        raise RuntimeError(
            f"EXECUTION_SLICE_EXHAUSTED:{repo}:{execution_slice.get('slice_id','UNKNOWN')}"
        )
    request_path.unlink(missing_ok=True)
    return execution_slice

def direction_request_outbox(state_root:Path)->list[dict]:
    """Render unresolved machine direction requests into connector-neutral governance messages."""
    root=state_root/"direction-requests"
    messages=[]
    if not root.is_dir():
        return messages
    for path in sorted(root.glob("*.json")):
        try:
            request=json.loads(path.read_text(encoding="utf-8"))
        except (OSError,json.JSONDecodeError,TypeError):
            continue
        if request.get("status")!="DIRECTION_REQUIRED":
            continue
        repo=request.get("repository")
        issues=request.get("issues",[])
        codes=[str(i.get("code")) for i in issues if isinstance(i,dict) and i.get("code")]
        detail=", ".join(codes) or str(request.get("reason","UNKNOWN"))
        messages.append({
            "kind":"PROJECT_DIRECTION_REQUIRED",
            "repository":repo,
            "exact_head":request.get("exact_head"),
            "title":f"Direction required: {repo}",
            "message":f"{repo} cannot safely select its next autonomous slice. Missing authority: {detail}. Supply authoritative project direction; do not invent requirements.",
            "issues":issues,
            "source":str(path),
        })
    return messages

def publish_direction_outbox(state_root:Path)->None:
    """Persist a transport-neutral escalation surface for Linear/ChatGPT/other governance connectors."""
    atomic(state_root/"direction-outbox.json",{
        "schema_version":1,
        "updated_at_utc":datetime.now(UTC).isoformat(),
        "messages":direction_request_outbox(state_root),
    })

def reserved_quarantine_repositories(state_root:Path)->set[str]:
    """Reserve only lanes whose ownership or preserved-work recovery remains quarantined."""
    path=state_root/"hands-off-grant-lifecycle.json"
    try:
        grants=json.loads(path.read_text(encoding="utf-8")).get("grants",{})
    except (OSError,json.JSONDecodeError,TypeError):
        return set()
    reserved=set()
    for record in grants.values():
        if not isinstance(record,dict):
            continue
        if record.get("state")=="QUARANTINED" or record.get("recovery_state")=="QUARANTINED":
            repo=record.get("repository")
            if isinstance(repo,str) and repo:
                reserved.add(repo)
    return reserved

def refresh_decision_learning(state_root:Path)->dict:
    """Reconcile terminal worker evidence into advisory decision-learning state."""
    ledger=state_root/"telemetry"/"jev-shadow"/"decisions.jsonl"
    records=load_jsonl(ledger)
    try:
        lifecycle=json.loads((state_root/"hands-off-grant-lifecycle.json").read_text(encoding="utf-8")).get("grants",{})
    except (OSError,json.JSONDecodeError,TypeError):
        lifecycle={}
    rows=[]
    for record in records:
        for grant_id,result in lifecycle.items():
            if not isinstance(result,dict):
                continue
            enriched=dict(result)
            enriched.setdefault("candidate_id",result.get("lane_id") or result.get("slice_id") or grant_id)
            row=attribute_decision_outcome(record,enriched)
            if row:
                rows.append(row)
    learning=compile_decision_learning(rows)
    payload={"schema_version":1,"attributed_outcomes":len(rows),"learning":learning,
             "updated_at_utc":datetime.now(UTC).isoformat()}
    atomic(state_root/"decision-learning.json",payload)
    return payload


def decision_learning_hint(state_root:Path,decision_class:str)->dict:
    try:
        payload=json.loads((state_root/"decision-learning.json").read_text(encoding="utf-8"))
        learning=payload.get("learning",{})
    except (OSError,json.JSONDecodeError,TypeError):
        learning={"policies":[]}
    return planning_hint(learning,decision_class)


def select_execution_target(state_root:Path,activation:dict,products:list[str])->tuple[str,str]:
    """Select one READY repository deterministically; unavailable authority isolates only that lane."""
    reserved=reserved_quarantine_repositories(state_root)
    candidates=sorted(
        (repo,head)
        for name in products
        for repo,head in activation["products"][name]["repositories"].items()
        if head and repo not in reserved
    )
    if not candidates:
        raise RuntimeError("NO_EXECUTION_TARGET")
    cursor_path=state_root/"execution-target-cursor.json"
    try:
        previous=json.loads(cursor_path.read_text(encoding="utf-8")).get("repository")
    except (OSError,json.JSONDecodeError,TypeError):
        previous=None
    ordered=candidates
    learning_hint=decision_learning_hint(state_root,"portfolio:allocation")
    # Learning remains advisory. Record it beside the deterministic selection
    # so later evidence can prove whether following it would have helped.
    atomic(state_root/"decision-learning-hint.json",{
        "schema_version":1,"decision_class":"portfolio:allocation",
        "hint":learning_hint,"recorded_at_utc":datetime.now(UTC).isoformat(),
    })
    if previous:
        after=[item for item in candidates if item[0]>previous]
        before=[item for item in candidates if item[0]<=previous]
        ordered=after+before
    blocked=[]
    decisions=[]
    for repo,head in ordered:
        product=product_for_repository(repo)
        try:
            execution=load_execution_slice(state_root,repo,head)
        except RuntimeError as exc:
            reason=str(exc)
            decisions.append({"repository":repo,"product":product,"head_sha":head,
                              "outcome":"NOT_READY","reason":reason})
            blocked.append(f"{repo}={reason}")
            continue
        if execution.get("status")=="NO_WORK":
            decisions.append({"repository":repo,"product":product,"head_sha":head,
                              "outcome":"NO_AUTHORIZED_SLICE","reason":"NO_WORK"})
            blocked.append(f"{repo}=NO_WORK")
            continue
        conflict=conflicting_nonterminal_grant(state_root,repo,execution)
        if conflict:
            decisions.append({"repository":repo,"product":product,"head_sha":head,
                              "outcome":"ALREADY_OWNED","reason":"NONTERMINAL_GRANT",
                              "grant_id":conflict.get("grant_id"),"grant_state":conflict.get("state")})
            blocked.append(f"{repo}=ALREADY_OWNED:{conflict.get('grant_id')}")
            continue
        decisions.append({"repository":repo,"product":product,"head_sha":head,
                          "outcome":"SELECTED","reason":"READY_EXECUTION_SLICE"})
        atomic(state_root/"dispatch-decision.json",{"schema_version":1,
            "recorded_at_utc":datetime.now(UTC).isoformat(),"selected_repository":repo,
            "selected_product":product,"decisions":decisions})
        atomic(cursor_path,{"repository":repo})
        return repo,head
    atomic(state_root/"dispatch-decision.json",{"schema_version":1,
        "recorded_at_utc":datetime.now(UTC).isoformat(),"selected_repository":None,
        "selected_product":None,"decisions":decisions})
    if blocked and all(item.endswith("=NO_WORK") for item in blocked):
        raise RuntimeError("ALL_EXECUTION_LANES_COMPLETE")
    detail=";".join(blocked) or "UNKNOWN"
    raise RuntimeError(f"NO_READY_EXECUTION_SLICE:{detail}")

ISSUE_LEASE_SECONDS=60
RECOVERY_GENERATION_LEASE_SECONDS=60

def recover_stale_issued_grants(state_root:Path,now:datetime|None=None)->list[str]:
    path=state_root/"hands-off-grant-lifecycle.json"
    lifecycle_lock=_acquire_lifecycle_lock(path)
    try:
        """Release an unclaimed issuance only when its issuer is proven dead after the issue lease."""
        if not path.exists():
            return []
        try:
            payload=json.loads(path.read_text(encoding="utf-8"))
            grants=payload.get("grants",{})
        except (OSError,json.JSONDecodeError,TypeError):
            raise RuntimeError("GRANT_LIFECYCLE_UNREADABLE")
        current=now or datetime.now(UTC)
        recovered=[]; changed=False
        for grant_id,record in grants.items():
            if not isinstance(record,dict) or record.get("state")!="ISSUED":
                continue
            try:
                issued=datetime.fromisoformat(record["issued_at"])
                if issued.tzinfo is None:
                    issued=issued.replace(tzinfo=UTC)
            except (KeyError,TypeError,ValueError):
                continue
            if (current-issued).total_seconds()<=ISSUE_LEASE_SECONDS:
                continue
            liveness=worker_liveness(record.get("issuer"))
            record["last_issue_liveness_check_at"]=current.isoformat()
            record["issuer_liveness"]=liveness
            changed=True
            if liveness=="DEAD":
                record["state"]="ABANDONED"
                record["finished_at"]=current.isoformat()
                record["result_status"]="ISSUE_LEASE_EXPIRED_ISSUER_DEAD"
                recovered.append(grant_id)
            elif liveness=="UNKNOWN":
                checks=int(record.get("issue_unknown_liveness_checks",0))+1
                record["issue_unknown_liveness_checks"]=checks
                if checks>=UNKNOWN_LIVENESS_MAX_CHECKS:
                    record["state"]="QUARANTINED"
                    record["finished_at"]=current.isoformat()
                    record["result_status"]="ISSUE_LEASE_UNKNOWN_QUARANTINED"
            else:
                record["issue_unknown_liveness_checks"]=0
                record["result_status"]="ISSUE_LEASE_EXPIRED_ISSUER_ALIVE"
        if changed:
            atomic(path,payload)
        return recovered
    finally:
        _release_lifecycle_lock(lifecycle_lock)

def slice_paths_conflict(left:dict,right:dict)->bool:
    """Fail closed unless two slices prove disjoint mutation authority."""
    a=left.get("allowed_paths"); b=right.get("allowed_paths")
    if not isinstance(a,list) or not isinstance(b,list) or not a or not b:
        return True
    def norm(path):
        return str(path).replace("\\","/").strip("/")
    for x in map(norm,a):
        for y in map(norm,b):
            if not x or not y or x==y or x.startswith(y+"/") or y.startswith(x+"/"):
                return True
    return False

def select_conflict_free_slices(slices:list[dict],limit:int|None=None)->list[dict]:
    """Greedily select a deterministic maximal set of pairwise-disjoint slices."""
    selected=[]
    for candidate in slices:
        if any(slice_paths_conflict(candidate,owned) for owned in selected):
            continue
        selected.append(candidate)
        if limit is not None and len(selected)>=limit:
            break
    return selected

def ready_task_batch(state_root:Path,activation:dict,product:str,limit:int)->list[tuple[str,str,dict]]:
    """Plan a bounded conflict-free task batch from exact-head capability graphs."""
    candidates=[]
    repositories=activation.get("products",{}).get(product,{}).get("repositories",{})
    for repo,head in sorted(repositories.items()):
        if not head:
            continue
        path=state_root/"capability-graphs"/f"{repo.replace('/','__')}.json"
        try:
            payload=json.loads(path.read_text(encoding="utf-8"))
        except (OSError,json.JSONDecodeError):
            continue
        derived=derive_slices(payload,repository=repo,exact_head=head)
        if derived.get("status")!="READY":
            continue
        for task in derived.get("slices",[]):
            if conflicting_nonterminal_grant(state_root,repo,task) is None:
                candidates.append((repo,head,task))
    selected=[]
    for candidate in candidates:
        repo,head,task=candidate
        if any(repo==r and slice_paths_conflict(task,other) for r,_,other in selected):
            continue
        selected.append(candidate)
        if len(selected)>=limit:
            break
    return selected

def conflicting_nonterminal_grant(state_root:Path,repo:str,execution_slice:dict)->dict|None:
    """Return only ownership whose bounded path authority conflicts."""
    path=state_root/"hands-off-grant-lifecycle.json"
    if not path.exists():
        return None
    try:
        grants=json.loads(path.read_text(encoding="utf-8")).get("grants",{})
    except (OSError,json.JSONDecodeError,TypeError):
        raise RuntimeError("GRANT_LIFECYCLE_UNREADABLE")
    target=(execution_slice.get("capability_id"),execution_slice.get("slice_id"),
            execution_slice.get("repository"),execution_slice.get("exact_head"))
    for grant_id,record in grants.items():
        if not isinstance(record,dict):
            continue
        if record.get("state") not in {"ISSUED","CLAIMED","QUARANTINED"} and not record.get("recovery_generation"):
            continue
        authority=record.get("execution_slice") or {}
        if repo in record.get("authorized_repositories",[]) and slice_paths_conflict(execution_slice,authority):
            return {"grant_id":grant_id,"state":record.get("state"),
                    "worker":record.get("worker"),"execution_slice":authority}
    return None

def _acquire_os_file_lock(handle)->bool:
    """Acquire a nonblocking kernel-owned one-byte lock."""
    handle.seek(0,os.SEEK_END)
    if handle.tell()==0:
        handle.write(b"0"); handle.flush()
    handle.seek(0)
    try:
        if os.name=="nt":
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        return True
    except (OSError,BlockingIOError):
        return False

def _release_os_file_lock(handle)->None:
    handle.seek(0)
    if os.name=="nt":
        import msvcrt
        msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(),fcntl.LOCK_UN)

def acquire_supervisor_instance_lock(state_root:Path)->dict|None:
    """Allow exactly one physical supervisor process per state root."""
    state_root.mkdir(parents=True,exist_ok=True)
    path=state_root/"supervisor-instance.lock"
    handle=path.open("a+b",buffering=0)
    if not _acquire_os_file_lock(handle):
        handle.close()
        return None
    lock_id=str(uuid.uuid4())
    worker={"host":socket.gethostname(),"pid":os.getpid(),
            "process_start_fingerprint":process_start_fingerprint(),
            "instance_id":uuid.uuid4().hex}
    metadata=state_root/"supervisor-instance.lock.json"
    atomic(metadata,{"schema_version":1,"lock_id":lock_id,"worker":worker,
                     "acquired_at_utc":datetime.now(UTC).isoformat()})
    return {"path":path,"metadata_path":metadata,"handle":handle,"lock_id":lock_id,"worker":worker}

def release_supervisor_instance_lock(lock:dict)->None:
    """Release this process's supervisor fence without deleting another owner's metadata."""
    handle=lock["handle"]; metadata=lock["metadata_path"]
    try:
        if metadata.exists():
            try:
                current=json.loads(metadata.read_text(encoding="utf-8"))
                if current.get("lock_id")==lock.get("lock_id"):
                    metadata.unlink()
            except (OSError,json.JSONDecodeError,TypeError):
                pass
    finally:
        try:
            _release_os_file_lock(handle)
        finally:
            handle.close()

def acquire_grant_issue_lock(state_root:Path)->dict:
    """Acquire a crash-safe kernel-owned single-writer fence for grant issuance."""
    state_root.mkdir(parents=True,exist_ok=True)
    path=state_root/"grant-issuance.lock"
    handle=path.open("a+b",buffering=0)
    if not _acquire_os_file_lock(handle):
        handle.close()
        raise RuntimeError("GRANT_ISSUANCE_BUSY")
    worker={"host":socket.gethostname(),"pid":os.getpid(),
            "incarnation_id":uuid.uuid4().hex,
            "process_start_fingerprint":process_start_fingerprint(),
            "lease_token":uuid.uuid4().hex}
    lock_id=str(uuid.uuid4())
    metadata=state_root/"grant-issuance.lock.json"
    atomic(metadata,{"schema_version":2,"lock_id":lock_id,"worker":worker,
                     "created_at_utc":datetime.now(UTC).isoformat()})
    return {"path":path,"metadata_path":metadata,"handle":handle,
            "lock_id":lock_id,"worker":worker}

def release_grant_issue_lock(lock:dict)->None:
    """Release only this process's kernel-owned issuance fence."""
    handle=lock["handle"]; metadata=lock["metadata_path"]
    error=None
    try:
        if metadata.exists():
            try:
                current=json.loads(metadata.read_text(encoding="utf-8"))
                if current.get("lock_id")!=lock.get("lock_id"):
                    error=RuntimeError("GRANT_ISSUANCE_LOCK_OWNERSHIP_LOST")
                else:
                    metadata.unlink()
            except (OSError,json.JSONDecodeError,TypeError):
                error=RuntimeError("GRANT_ISSUANCE_LOCK_METADATA_UNREADABLE")
    finally:
        try:
            _release_os_file_lock(handle)
        finally:
            handle.close()
    if error is not None:
        raise error

def write_execution_grant(repo_root:Path,state_root:Path,products:list[str],
                          planned_target:tuple[str,str,dict]|None=None)->Path:
    """Issue one exact-head task grant, optionally from a preplanned task batch."""
    lock=acquire_grant_issue_lock(state_root)
    try:
        activation_path=state_root/"hands-off-activation.json"
        activation_bytes=activation_path.read_bytes()
        activation=json.loads(activation_bytes)
        contract_path=repo_root/"engineering"/"hands-off-evolution.json"
        contract_bytes=contract_path.read_bytes()
        recover_stale_issued_grants(state_root)
        if planned_target is None:
            repo,exact_head=select_execution_target(state_root,activation,products)
            execution_slice=load_execution_slice(state_root,repo,exact_head)
        else:
            repo,exact_head,execution_slice=planned_target
            if execution_slice.get("repository")!=repo or execution_slice.get("exact_head")!=exact_head:
                raise RuntimeError("PLANNED_SLICE_IDENTITY_MISMATCH")
            if not any(repo in activation["products"][name]["repositories"] and
                       activation["products"][name]["repositories"][repo]==exact_head
                       for name in products if name in activation.get("products",{})):
                raise RuntimeError("PLANNED_SLICE_HEAD_STALE")
        selected_products={
            name for name in products
            if repo in activation["products"][name]["repositories"]
        }
        conflict=conflicting_nonterminal_grant(state_root,repo,execution_slice)
        if conflict:
            raise RuntimeError(f"EXECUTION_SLICE_ALREADY_OWNED:{conflict['grant_id']}:{conflict['state']}")
        grant_id=str(uuid.uuid4())
        runner_id=execution_slice.get("runner_id") or select_task_runner(execution_slice)
        runtime_id=execution_slice.get("execution_runtime") or runtime_for_task(execution_slice)
        execution_slice={**execution_slice,"runner_id":runner_id,"execution_runtime":runtime_id}
        grant={"schema_version":5,
               "grant_id":grant_id,
               "contract_sha256":hashlib.sha256(contract_bytes).hexdigest(),
               "activation_sha256":hashlib.sha256(activation_bytes).hexdigest(),
               "products":{
            name:{"repositories":activation["products"][name]["repositories"]}
            for name in selected_products},
               "execution_slice":execution_slice}
        path=state_root/"hands-off-execution-grants"/f"{grant_id}.json"
        atomic(path,grant)
        lifecycle=state_root/"hands-off-grant-lifecycle.json"
        lifecycle_lock=_acquire_lifecycle_lock(lifecycle)
        try:
            try:
                payload=json.loads(lifecycle.read_text(encoding="utf-8")) if lifecycle.exists() else {"schema_version":1,"grants":{}}
                grants=payload.setdefault("grants",{})
            except (OSError,json.JSONDecodeError,TypeError):
                raise RuntimeError("GRANT_LIFECYCLE_UNREADABLE")
            for existing_id,existing in grants.items():
                if not isinstance(existing,dict):
                    continue
                owns=existing.get("state") in {"ISSUED","CLAIMED","QUARANTINED"} or bool(existing.get("recovery_generation"))
                existing_repos=set(existing.get("authorized_repositories",[]))
                if existing.get("repository"):
                    existing_repos.add(existing["repository"])
                existing_slice=existing.get("execution_slice") or {}
                if owns and repo in existing_repos and slice_paths_conflict(execution_slice,existing_slice):
                    raise RuntimeError(f"EXECUTION_SLICE_ALREADY_OWNED:{existing_id}:{existing.get('state')}")
            issued=datetime.now(UTC).isoformat()
            authorized_repositories=sorted({
                granted_repo
                for value in grant["products"].values()
                for granted_repo in value["repositories"]
            })
            grants[grant_id]={"state":"ISSUED","issued_at":issued,
                          "authorized_repositories":authorized_repositories,"expected_sha":exact_head,
                          "capability_id":execution_slice["capability_id"],
                          "slice_id":execution_slice["slice_id"],
                          "runner_id":runner_id,"execution_runtime":runtime_id,
                          "execution_slice":execution_slice,
                              "issuer":lock["worker"]}
            atomic(lifecycle,payload)
        finally:
            _release_lifecycle_lock(lifecycle_lock)
        return path
    finally:
        release_grant_issue_lock(lock)

def finalize_unclaimed_grant(grant_path:Path,returncode:int)->bool:
    lifecycle=grant_path.with_name("hands-off-grant-lifecycle.json")
    if grant_path.parent.name=="hands-off-execution-grants":
        lifecycle=grant_path.parent.parent/"hands-off-grant-lifecycle.json"
    lifecycle_lock=_acquire_lifecycle_lock(lifecycle)
    try:
        """Terminalize an issued grant when the builder exits before claiming it."""
        try:
            grant_id=json.loads(grant_path.read_text(encoding="utf-8"))["grant_id"]
            payload=json.loads(lifecycle.read_text(encoding="utf-8"))
            record=payload["grants"][grant_id]
        except (OSError,json.JSONDecodeError,KeyError,TypeError):
            return False
        if record.get("state")!="ISSUED":
            return False
        record["state"]="ABANDONED"
        record["finished_at"]=datetime.now(UTC).isoformat()
        record["result_status"]=f"BUILDER_EXITED_WITHOUT_CLAIM:{returncode}"
        atomic(lifecycle,payload)
        return True
    finally:
        _release_lifecycle_lock(lifecycle_lock)

def run_builder(repo_root:Path,workspace_root:Path,state_root:Path,products:list[str]|None=None,
                worker_id:str|None=None,planned_target:tuple[str,str,dict]|None=None)->tuple[int,str|None]:
    worker_dir=state_root/"worker-state"/worker_id if worker_id else state_root
    worker_dir.mkdir(parents=True,exist_ok=True)
    builder_state=worker_dir/"builder.json"
    builder_output=worker_dir/"builder-last-output.txt"
    if not products:
        builder_output.write_text("EXECUTION_GRANT_REQUIRED\n",encoding="utf-8")
        return 2,None
    try:
        grant_path=None
        for attempt in range(3):
            try:
                grant_path=write_execution_grant(repo_root,state_root,products,planned_target=planned_target)
                break
            except RuntimeError as exc:
                if str(exc)!="GRANT_ISSUANCE_BUSY":
                    raise
                if attempt==2:
                    atomic(builder_state,{"status":"OWNERSHIP_HELD","reason":str(exc)})
                    return 0,None
                time.sleep(0.05*(attempt+1))
        assert grant_path is not None
    except RuntimeError as exc:
        if str(exc)=="ALL_EXECUTION_LANES_COMPLETE":
            atomic(builder_state,{"status":"NO_WORK","reason":"ALL_EXECUTION_LANES_COMPLETE"})
            builder_output.write_text("ALL_EXECUTION_LANES_COMPLETE\n",encoding="utf-8")
            return 0,None
        if str(exc).startswith("EXECUTION_SLICE_ALREADY_OWNED:"):
            atomic(builder_state,{"status":"OWNERSHIP_HELD","reason":str(exc)})
            builder_output.write_text(str(exc)+"\n",encoding="utf-8")
            return 0,None
        raise
    invocation_id=uuid.uuid4().hex
    decision_path=state_root/"dispatch-decision.json"
    try:
        decision=json.loads(decision_path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError,TypeError):
        decision={}
    if isinstance(decision,dict):
        decision["invocation_id"]=invocation_id
        decision["grant_path"]=str(grant_path)
        atomic(decision_path,decision)
    grant_payload=json.loads(grant_path.read_text(encoding="utf-8"))
    grant_authority=grant_payload.get("execution_slice",{})
    runner_id=grant_authority.get("runner_id")
    runtime_id=grant_authority.get("execution_runtime")
    if not runner_id or not runtime_id:
        raise RuntimeError("EXECUTION_GRANT_IDENTITY_REQUIRED")
    cmd=[sys.executable,str(repo_root/"scripts"/"autonomous_slice_builder.py"),
        "--workspace-root",str(workspace_root),"--state",str(builder_state),
        "--execution-grant",str(grant_path),"--invocation-id",invocation_id,
        "--runner-id",runner_id,"--runtime-id",runtime_id]
    p=subprocess.run(cmd,
        cwd=repo_root,text=True,capture_output=True,check=False)
    builder_output.write_text((p.stdout or "")+(p.stderr or ""),encoding="utf-8")
    finalize_unclaimed_grant(grant_path,p.returncode)
    return p.returncode,invocation_id

def run_builder_for_product(repo_root:Path,workspace_root:Path,state_root:Path,product:str,
                            worker_id:str|None=None,planned_target:tuple[str,str,dict]|None=None)->tuple[int,str|None]:
    """Run one task-scoped builder with worker-isolated result state."""
    return run_builder(repo_root,workspace_root,state_root,[product],worker_id=worker_id,
                       planned_target=planned_target)


def harvest_parallel_builders(pool:ParallelWorkerPool,state_root:Path)->dict:
    """Persist completed worker outcomes without blocking other product lanes."""
    completed=pool.harvest()
    rows={}
    for worker_id,result in completed.items():
        if isinstance(result,BaseException):
            rows[worker_id]={"status":"WORKER_EXCEPTION","error_class":type(result).__name__}
        else:
            rc,invocation_id=result
            worker_state=state_root/"worker-state"/worker_id
            ok,status=builder_health(worker_state,rc,invocation_id)
            rows[worker_id]={"status":status,"returncode":rc,"healthy":ok,
                             "invocation_id":invocation_id}
    if rows:
        atomic(state_root/"parallel-worker-results.json",{
            "schema_version":1,"completed":rows,
            "recorded_at_utc":datetime.now(UTC).isoformat(),
        })
    return rows


def runner_requirements_for_task(task:dict)->set[str]:
    """Read explicit runner requirements; legacy slices use the proven local code runner."""
    raw=task.get("runner_requirements",["code","local","windows","offline"])
    if not isinstance(raw,list) or not raw or any(not isinstance(x,str) or not x.strip() for x in raw):
        raise RuntimeError("INVALID_RUNNER_REQUIREMENTS")
    return set(raw)

def select_task_runner(task:dict,registry:RunnerRegistry=DEFAULT_RUNNERS,
                       running:dict[str,int]|None=None)->str:
    return registry.select(runner_requirements_for_task(task),running).runner_id

def runtime_for_task(task:dict)->str:
    """Choose execution environment independently from the reasoning runner."""
    raw=task.get("runtime_requirements",[])
    if raw is None:
        raw=[]
    if not isinstance(raw,list) or any(not isinstance(x,str) or not x.strip() for x in raw):
        raise RuntimeError("INVALID_RUNTIME_REQUIREMENTS")
    requirements=set(raw)
    machine_specific={"unreal","ollama","laptop-actor"}
    if requirements & machine_specific:
        runtime_id="laptop-actor"
        require_runtime_capabilities(runtime_id,sorted(requirements & machine_specific))
        return runtime_id
    return "native-windows"

def dispatch_parallel_builders(pool:ParallelWorkerPool,repo_root:Path,workspace_root:Path,
                               state_root:Path,products:list[str])->list[str]:
    """Keep product workers saturated; repository grants remain the mutation authority."""
    dispatched=[]
    try:
        activation=json.loads((state_root/"hands-off-activation.json").read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError,TypeError):
        activation={}
    task_batches={
        product:ready_task_batch(state_root,activation,product,pool.max_workers_per_product)
        for product in products
    }
    assigned={product:0 for product in products}
    for slot in pool.available_slots(products):
        batch=task_batches.get(slot.product,[])
        index=assigned.get(slot.product,0)
        if index>=len(batch):
            continue
        planned_target=batch[index]
        try:
            runner_id=select_task_runner(planned_target[2])
            runtime_id=runtime_for_task(planned_target[2])
        except (RuntimeError,ValueError):
            continue
        planned_target[2]["execution_runtime"]=runtime_id
        # Current execution adapter is the local Aider/Ollama builder. Other
        # registered runners remain ineligible until their adapters are enabled.
        if runner_id!="aider-ollama":
            continue
        assigned[slot.product]=index+1
        try:
            RUNTIME_LEASES.acquire(runtime_id,slot.worker_id)
        except RuntimeError:
            continue
        def leased_builder(*args,_worker_id=slot.worker_id,**kwargs):
            try:
                return run_builder_for_product(*args,**kwargs)
            finally:
                RUNTIME_LEASES.release(_worker_id)
        try:
            worker_id=pool.submit(slot,leased_builder,
                                  repo_root,workspace_root,state_root,slot.product,slot.worker_id,
                                  planned_target)
        except RuntimeError:
            RUNTIME_LEASES.release(slot.worker_id)
            continue
        dispatched.append(worker_id)
    atomic(state_root/"parallel-worker-pool.json",{
        "schema_version":2,"pool":pool.snapshot(),"runtime_leases":RUNTIME_LEASES.snapshot(),"dispatched":dispatched,
        "recorded_at_utc":datetime.now(UTC).isoformat(),
    })
    return dispatched


CLAIM_LEASE_SECONDS=1800
UNKNOWN_LIVENESS_MAX_CHECKS=5

def process_start_fingerprint(pid:int|None=None)->str|None:
    """Return a lightweight OS-backed process creation identity."""
    pid=pid or os.getpid()
    if sys.platform.startswith("linux"):
        try:
            stat=Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            start_ticks=stat[stat.rfind(")")+2:].split()[19]
            boot_id=Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
            return f"linux:{boot_id}:{start_ticks}"
        except (OSError,IndexError,ValueError):
            return None
    if sys.platform=="win32":
        try:
            import ctypes
            from ctypes import wintypes
            kernel32=ctypes.WinDLL("kernel32",use_last_error=True)
            handle=kernel32.OpenProcess(0x1000,False,pid)
            if not handle:
                return None
            creation=wintypes.FILETIME(); exit_time=wintypes.FILETIME()
            kernel=wintypes.FILETIME(); user=wintypes.FILETIME()
            try:
                if not kernel32.GetProcessTimes(handle,ctypes.byref(creation),ctypes.byref(exit_time),
                                                ctypes.byref(kernel),ctypes.byref(user)):
                    return None
                ticks=(creation.dwHighDateTime<<32)|creation.dwLowDateTime
                return f"windows:{ticks}"
            finally:
                kernel32.CloseHandle(handle)
        except (AttributeError,OSError):
            return None
    return None

def worker_liveness(worker:dict|None)->str:
    """Return ALIVE, DEAD, or UNKNOWN without guessing across hosts or legacy state."""
    if not isinstance(worker,dict):
        return "UNKNOWN"
    if worker.get("host")!=socket.gethostname():
        return "UNKNOWN"
    try:
        pid=int(worker["pid"])
    except (KeyError,TypeError,ValueError):
        return "UNKNOWN"
    if pid<=0:
        return "UNKNOWN"
    expected=worker.get("process_start_fingerprint")
    if expected:
        try:
            from scripts.autonomous_slice_builder import process_start_fingerprint as builder_process_start_fingerprint
            actual=builder_process_start_fingerprint(pid)
        except (ImportError,OSError):
            return "UNKNOWN"
        if actual is None:
            return "DEAD"
        if actual!=expected:
            return "DEAD"
        return "ALIVE"
    try:
        os.kill(pid,0)
    except ProcessLookupError:
        return "DEAD"
    except (PermissionError,OSError):
        return "UNKNOWN"
    return "ALIVE"

def recover_stale_recovery_generations(state_root:Path,now:datetime|None=None,liveness_probe=worker_liveness)->list[str]:
    """Release recovery generations only when their owning supervisor is proven dead."""
    path=state_root/"hands-off-grant-lifecycle.json"
    lock=_acquire_lifecycle_lock(path)
    try:
        if not path.exists():
            return []
        try:
            payload=json.loads(path.read_text(encoding="utf-8"))
            grants=payload.get("grants",{})
        except (OSError,json.JSONDecodeError,TypeError):
            return []
        current=now or datetime.now(UTC)
        released=[]; changed=False
        for grant_id,record in grants.items():
            if not isinstance(record,dict) or not record.get("recovery_generation"):
                continue
            try:
                started=datetime.fromisoformat(record["recovery_generation_started_at"])
                if started.tzinfo is None:
                    started=started.replace(tzinfo=UTC)
            except (KeyError,TypeError,ValueError):
                record["recovery_generation_liveness"]="UNKNOWN"
                record["recovery_generation_result"]="RECOVERY_GENERATION_TIMESTAMP_INVALID"
                record["recovery_state"]="QUARANTINED"
                record["recovery_quarantine_reason"]="RECOVERY_GENERATION_TIMESTAMP_INVALID"
                changed=True
                continue
            if (current-started).total_seconds()<=RECOVERY_GENERATION_LEASE_SECONDS:
                continue
            liveness=liveness_probe(record.get("recovery_generation_worker"))
            record["recovery_generation_last_liveness_check_at"]=current.isoformat()
            record["recovery_generation_liveness"]=liveness
            changed=True
            if liveness=="DEAD":
                record.pop("recovery_generation",None)
                record.pop("recovery_generation_started_at",None)
                record.pop("recovery_generation_worker",None)
                record["recovery_generation_released_at"]=current.isoformat()
                record["recovery_generation_result"]="RECOVERY_GENERATION_OWNER_DEAD"
                released.append(grant_id)
            elif liveness=="ALIVE":
                record["recovery_generation_result"]="RECOVERY_GENERATION_OWNER_ALIVE"
            else:
                checks=int(record.get("recovery_generation_unknown_checks",0))+1
                record["recovery_generation_unknown_checks"]=checks
                record["recovery_generation_result"]="RECOVERY_GENERATION_OWNER_UNKNOWN"
                if checks>=UNKNOWN_LIVENESS_MAX_CHECKS:
                    record["recovery_state"]="QUARANTINED"
                    record["recovery_quarantine_reason"]="RECOVERY_GENERATION_OWNER_UNKNOWN"
        if changed:
            atomic(path,payload)
        return released
    finally:
        _release_lifecycle_lock(lock)

def recover_stale_grant_claims(state_root:Path,now:datetime|None=None,liveness_probe=worker_liveness)->list[str]:
    try:
        """Abandon an expired claim only when its owning worker is proven dead."""
        path=state_root/"hands-off-grant-lifecycle.json"
        lifecycle_lock=_acquire_lifecycle_lock(path)
        if not path.exists():
            return []
        try:
            payload=json.loads(path.read_text(encoding="utf-8"))
            grants=payload.get("grants",{})
        except (OSError,json.JSONDecodeError,TypeError):
            return []
        current=now or datetime.now(UTC)
        abandoned=[]; changed=False
        for grant_id,record in grants.items():
            if not isinstance(record,dict) or record.get("state")!="CLAIMED":
                continue
            try:
                heartbeat=datetime.fromisoformat(record.get("last_heartbeat_at",record["claimed_at"]))
                if heartbeat.tzinfo is None:
                    heartbeat=heartbeat.replace(tzinfo=UTC)
            except (KeyError,TypeError,ValueError):
                continue
            age=(current-heartbeat).total_seconds()
            if age <= CLAIM_LEASE_SECONDS:
                continue
            liveness=liveness_probe(record.get("worker"))
            record["last_liveness_check_at"]=current.isoformat()
            record["worker_liveness"]=liveness
            changed=True
            if liveness=="DEAD":
                record["state"]="ABANDONED"
                record["finished_at"]=current.isoformat()
                record["result_status"]="CLAIM_LEASE_EXPIRED_WORKER_DEAD"
                abandoned.append(grant_id)
            elif liveness=="UNKNOWN":
                checks=int(record.get("unknown_liveness_checks",0))+1
                record["unknown_liveness_checks"]=checks
                record.setdefault("unknown_liveness_first_seen_at",current.isoformat())
                if checks>=UNKNOWN_LIVENESS_MAX_CHECKS:
                    record["state"]="QUARANTINED"
                    record["finished_at"]=current.isoformat()
                    record["result_status"]="CLAIM_LEASE_UNKNOWN_QUARANTINED"
                else:
                    record["result_status"]="CLAIM_LEASE_EXPIRED_WORKER_UNKNOWN"
            else:
                record["unknown_liveness_checks"]=0
                record.pop("unknown_liveness_first_seen_at",None)
                record["result_status"]="CLAIM_LEASE_EXPIRED_WORKER_ALIVE"
        if changed:
            atomic(path,payload)
        return abandoned

    finally:
        _release_lifecycle_lock(lifecycle_lock)

def classify_orphan_branch(repo:str,ws:Path,branch:str)->tuple[str,dict]:
    """Classify clean autonomous branch state without mutating it."""
    base=subprocess.run(["git","merge-base","HEAD","origin/main"],cwd=ws,text=True,capture_output=True,check=False)
    head=subprocess.run(["git","rev-parse","HEAD"],cwd=ws,text=True,capture_output=True,check=False)
    if base.returncode or head.returncode:
        return "ORPHAN_HISTORY_UNREADABLE",{}
    base_sha=base.stdout.strip(); head_sha=head.stdout.strip()
    if not base_sha or not head_sha:
        return "ORPHAN_HISTORY_UNREADABLE",{}
    if head_sha==base_sha:
        return "ORPHAN_NO_DIFF",{"base_sha":base_sha,"head_sha":head_sha}
    remote=subprocess.run(["git","ls-remote","--heads","origin",branch],cwd=ws,text=True,capture_output=True,check=False)
    if remote.returncode:
        return "ORPHAN_REMOTE_UNREADABLE",{"base_sha":base_sha,"head_sha":head_sha}
    remote_head=remote.stdout.split()[0] if remote.stdout.strip() else ""
    pr=subprocess.run(["gh","pr","list","--repo",repo,"--head",branch,"--state","open","--json","number,url,headRefOid"],
                      cwd=ws,text=True,capture_output=True,check=False)
    if pr.returncode:
        return "ORPHAN_PR_STATE_UNREADABLE",{"base_sha":base_sha,"head_sha":head_sha}
    try:
        prs=json.loads(pr.stdout)
    except json.JSONDecodeError:
        return "ORPHAN_PR_STATE_UNREADABLE",{"base_sha":base_sha,"head_sha":head_sha}
    evidence={"base_sha":base_sha,"head_sha":head_sha,"remote_head_sha":remote_head}
    if prs:
        evidence["pr_number"]=prs[0].get("number"); evidence["pr_url"]=prs[0].get("url")
        if prs[0].get("headRefOid")==head_sha:
            return "ORPHAN_PR_RECOVERABLE",evidence
        return "ORPHAN_PR_HEAD_MISMATCH",evidence
    if remote_head==head_sha:
        return "ORPHAN_PUSHED_NO_PR",evidence
    if not remote_head:
        return "ORPHAN_LOCAL_COMMIT_UNPUSHED",evidence
    return "ORPHAN_REMOTE_HEAD_MISMATCH",evidence

def resume_orphan_branch(repo:str,ws:Path,branch:str,classification:str,evidence:dict)->str:
    """Resume only exact-head committed orphan work; never repair or rewrite history here."""
    expected=evidence.get("head_sha","")
    actual=subprocess.run(["git","rev-parse","HEAD"],cwd=ws,text=True,capture_output=True,check=False)
    if actual.returncode or actual.stdout.strip()!=expected:
        return "RECOVERY_HEAD_CHANGED"
    if classification=="ORPHAN_LOCAL_COMMIT_UNPUSHED":
        preflight=subprocess.run([sys.executable,"scripts/preflight.py"],cwd=ws,text=True,capture_output=True,check=False)
        if preflight.returncode:
            return "RECOVERY_PREFLIGHT_FAILED"
        pushed=subprocess.run(["git","push","-u","origin",branch],cwd=ws,text=True,capture_output=True,check=False)
        if pushed.returncode:
            return "RECOVERY_PUSH_FAILED"
        remote=subprocess.run(["git","ls-remote","--heads","origin",branch],cwd=ws,text=True,capture_output=True,check=False)
        if remote.returncode or not remote.stdout.strip() or remote.stdout.split()[0]!=expected:
            return "RECOVERY_REMOTE_HEAD_MISMATCH"
        classification="ORPHAN_PUSHED_NO_PR"
    if classification=="ORPHAN_PUSHED_NO_PR":
        remote_expected=evidence.get("remote_head_sha") or expected
        remote=subprocess.run(["git","ls-remote","--heads","origin",branch],cwd=ws,text=True,capture_output=True,check=False)
        if remote.returncode or not remote.stdout.strip() or remote.stdout.split()[0]!=remote_expected or remote_expected!=expected:
            return "RECOVERY_REMOTE_HEAD_MISMATCH"
        created=subprocess.run(["gh","pr","create","--repo",repo,"--base","main","--head",branch,
                                "--title","AUTO: recovered autonomous engineering slice",
                                "--body","Recovered exact-head autonomous work after worker lease expiry. Local preflight and remote SHA were verified before PR creation."],
                               cwd=ws,text=True,capture_output=True,check=False)
        return "RECOVERY_PR_OPENED" if created.returncode==0 else "RECOVERY_PR_CREATE_FAILED"
    return classification

def builder_artifacts_only(evidence:dict)->bool:
    """True only for captured zero-diff Aider bookkeeping, never product work."""
    prefixes=(".aider.chat.history.md",".aider.input.history",".aider.tags.cache.v4/")
    untracked=evidence.get("untracked",[])
    return (evidence.get("capture_status")=="CAPTURED"
        and evidence.get("diff_bytes")==0 and not evidence.get("changed_paths")
        and bool(untracked)
        and all(any(item.get("path","")==prefix or item.get("path","").startswith(prefix)
                    for prefix in prefixes) for item in untracked))

def preserved_work_evidence(ws:Path,base_sha:str)->dict:
    """Capture bounded, non-destructive identity for uncertain local work."""
    status=subprocess.run(["git","status","--porcelain"],cwd=ws,text=True,capture_output=True,check=False)
    head=subprocess.run(["git","rev-parse","HEAD"],cwd=ws,text=True,capture_output=True,check=False)
    branch=subprocess.run(["git","branch","--show-current"],cwd=ws,text=True,capture_output=True,check=False)
    names=subprocess.run(["git","diff","--name-only",base_sha],cwd=ws,text=True,capture_output=True,check=False)
    diff=subprocess.run(["git","diff","--binary",base_sha],cwd=ws,text=True,capture_output=True,check=False)
    if any(x.returncode for x in (status,head,branch,names,diff)):
        return {"capture_status":"UNREADABLE"}
    import hashlib
    payload=diff.stdout.encode("utf-8",errors="replace")
    untracked=subprocess.run(["git","ls-files","--others","--exclude-standard","-z"],cwd=ws,text=True,capture_output=True,check=False)
    if untracked.returncode:
        return {"capture_status":"UNREADABLE"}
    untracked_records=[]
    for rel in untracked.stdout.split(chr(0)):
        if not rel:
            continue
        path=ws/rel
        try:
            if path.is_symlink():
                target=os.readlink(path)
                data=target.encode("utf-8",errors="surrogateescape")
                kind="symlink"
            elif path.is_file():
                size=path.stat().st_size
                if size>10*1024*1024:
                    return {"capture_status":"UNTRACKED_FILE_TOO_LARGE","path":rel}
                data=path.read_bytes()
                kind="file"
            else:
                return {"capture_status":"UNTRACKED_SPECIAL_FILE","path":rel}
        except OSError:
            return {"capture_status":"UNTRACKED_FILE_UNREADABLE","path":rel}
        untracked_records.append({"path":rel,"kind":kind,"bytes":len(data),
                                  "sha256":hashlib.sha256(data).hexdigest()})
    return {
        "capture_status":"CAPTURED",
        "branch":branch.stdout.strip(),
        "head_sha":head.stdout.strip(),
        "status_porcelain":status.stdout[:12000],
        "changed_paths":[p for p in names.stdout.splitlines() if p][:500],
        "diff_sha256":hashlib.sha256(payload).hexdigest(),
        "diff_bytes":len(payload),
        "untracked":untracked_records,
        "repair_eligible":not bool(untracked_records),
    }

def revalidate_preserved_work(ws:Path,record:dict)->str:
    """Prove quarantined local work is byte-identical before any future repair."""
    expected=record.get("preserved_work_evidence")
    base=record.get("base_sha")
    if not isinstance(expected,dict) or expected.get("capture_status")!="CAPTURED" or not base:
        return "PRESERVED_EVIDENCE_MISSING"
    current=preserved_work_evidence(ws,base)
    if current.get("capture_status")!="CAPTURED":
        return "PRESERVED_WORK_UNREADABLE"
    identity=("branch","head_sha","status_porcelain","changed_paths","diff_sha256","diff_bytes","untracked","repair_eligible")
    if any(current.get(k)!=expected.get(k) for k in identity):
        return "PRESERVED_WORK_CHANGED"
    return "PRESERVED_WORK_EXACT"

def validate_preserved_work(ws:Path,record:dict)->str:
    """Run deterministic validation only between two exact identity proofs."""
    if revalidate_preserved_work(ws,record)!="PRESERVED_WORK_EXACT":
        return "PRESERVED_VALIDATION_IDENTITY_PRECHECK_FAILED"
    before=record.get("preserved_work_evidence",{}).get("diff_sha256")
    preflight=subprocess.run([sys.executable,"scripts/preflight.py"],cwd=ws,text=True,capture_output=True,check=False)
    if revalidate_preserved_work(ws,record)!="PRESERVED_WORK_EXACT":
        return "PRESERVED_VALIDATION_MUTATED_WORKSPACE"
    if preflight.returncode:
        return "PRESERVED_VALIDATION_FAILED"
    if not before:
        return "PRESERVED_VALIDATION_EVIDENCE_INCOMPLETE"
    return "PRESERVED_VALIDATION_PASSED"

def issue_preserved_recovery_capability(record:dict)->dict|None:
    """Issue inert, single-use authority bound to exact validated preserved work."""
    evidence=record.get("preserved_work_evidence",{})
    if record.get("preserved_work_validation")!="PRESERVED_VALIDATION_PASSED":
        return None
    if evidence.get("repair_eligible") is not True:
        return None
    required=("repository","base_sha","branch")
    if any(not record.get(k) for k in required) or not evidence.get("diff_sha256"):
        return None
    import secrets
    return {
        "capability_id":secrets.token_urlsafe(18),
        "state":"ISSUED",
        "repository":record["repository"],
        "base_sha":record["base_sha"],
        "branch":record["branch"],
        "preserved_diff_sha256":evidence["diff_sha256"],
        "preserved_head_sha":evidence.get("head_sha"),
        "issued_at":datetime.now(UTC).isoformat(),
    }

def consume_preserved_recovery_capability(ws:Path,record:dict)->str:
    """Consume exact recovery authority once; does not itself mutate product work."""
    cap=record.get("preserved_recovery_capability")
    if not isinstance(cap,dict) or cap.get("state")!="ISSUED":
        return "RECOVERY_CAPABILITY_UNAVAILABLE"
    if record.get("preserved_work_validation")!="PRESERVED_VALIDATION_PASSED":
        return "RECOVERY_CAPABILITY_VALIDATION_STALE"
    evidence=record.get("preserved_work_evidence",{})
    if evidence.get("repair_eligible") is not True:
        return "RECOVERY_CAPABILITY_REPAIR_NOT_AUTHORIZED"
    bindings={
        "repository":record.get("repository"),"base_sha":record.get("base_sha"),
        "branch":record.get("branch"),"preserved_diff_sha256":evidence.get("diff_sha256"),
        "preserved_head_sha":evidence.get("head_sha"),
    }
    if any(cap.get(k)!=v for k,v in bindings.items()):
        return "RECOVERY_CAPABILITY_BINDING_MISMATCH"
    if revalidate_preserved_work(ws,record)!="PRESERVED_WORK_EXACT":
        return "RECOVERY_CAPABILITY_IDENTITY_CHANGED"
    cap["state"]="CONSUMED"
    cap["consumed_at"]=datetime.now(UTC).isoformat()
    return "RECOVERY_CAPABILITY_CONSUMED"

def commit_preserved_work(ws:Path,record:dict)->str:
    """Consume exact recovery authority and durably preserve validated work in Git."""
    outcome=consume_preserved_recovery_capability(ws,record)
    if outcome!="RECOVERY_CAPABILITY_CONSUMED":
        return outcome
    cap=record["preserved_recovery_capability"]
    cap["outcome"]="MUTATION_STARTED"
    expected=record.get("preserved_work_evidence",{})
    add=subprocess.run(["git","add","-A"],cwd=ws,text=True,capture_output=True,check=False)
    if add.returncode:
        cap["outcome"]="STAGE_FAILED"
        return "PRESERVED_COMMIT_STAGE_FAILED"
    commit=subprocess.run(["git","commit","-m","chore: recover validated autonomous work"],cwd=ws,text=True,capture_output=True,check=False)
    if commit.returncode:
        cap["outcome"]="COMMIT_FAILED"
        return "PRESERVED_COMMIT_FAILED"
    head=subprocess.run(["git","rev-parse","HEAD"],cwd=ws,text=True,capture_output=True,check=False)
    branch=subprocess.run(["git","branch","--show-current"],cwd=ws,text=True,capture_output=True,check=False)
    if head.returncode or branch.returncode or branch.stdout.strip()!=record.get("branch"):
        cap["outcome"]="IDENTITY_FAILED"
        return "PRESERVED_COMMIT_IDENTITY_FAILED"
    new_head=head.stdout.strip()
    changed=subprocess.run(["git","diff","--binary",record["base_sha"],new_head],cwd=ws,text=True,capture_output=True,check=False)
    import hashlib
    if changed.returncode or hashlib.sha256(changed.stdout.encode("utf-8",errors="replace")).hexdigest()!=expected.get("diff_sha256"):
        cap["outcome"]="FINGERPRINT_MISMATCH"
        return "PRESERVED_COMMIT_FINGERPRINT_MISMATCH"
    record["execution_stage"]="COMMITTED"
    record["head_sha"]=new_head
    record["recovery_state"]="RECOVERING"
    record["recovery_result"]="PRESERVED_COMMIT_RECOVERED"
    cap["committed_head_sha"]=new_head
    cap["outcome"]="COMMITTED_EXACT"
    return "PRESERVED_COMMIT_RECOVERED"

def reconcile_consumed_recovery(ws:Path,record:dict)->str:
    """Adopt only a provably exact commit left by an interrupted recovery mutation."""
    cap=record.get("preserved_recovery_capability")
    if not isinstance(cap,dict) or cap.get("state")!="CONSUMED":
        return "CONSUMED_RECOVERY_NOT_APPLICABLE"
    if record.get("execution_stage")=="COMMITTED" and record.get("head_sha"):
        return "CONSUMED_RECOVERY_ALREADY_DURABLE"
    expected=record.get("preserved_work_evidence",{})
    base=record.get("base_sha"); branch_expected=record.get("branch")
    if not base or not branch_expected or not expected.get("diff_sha256"):
        return "CONSUMED_RECOVERY_EVIDENCE_MISSING"
    status=subprocess.run(["git","status","--porcelain"],cwd=ws,text=True,capture_output=True,check=False)
    head=subprocess.run(["git","rev-parse","HEAD"],cwd=ws,text=True,capture_output=True,check=False)
    branch=subprocess.run(["git","branch","--show-current"],cwd=ws,text=True,capture_output=True,check=False)
    if status.returncode or head.returncode or branch.returncode:
        return "CONSUMED_RECOVERY_WORKSPACE_UNREADABLE"
    if branch.stdout.strip()!=branch_expected:
        return "CONSUMED_RECOVERY_BRANCH_MISMATCH"
    current_head=head.stdout.strip()
    if current_head==base:
        return "CONSUMED_RECOVERY_NO_COMMIT"
    if status.stdout.strip():
        return "CONSUMED_RECOVERY_DIRTY_AFTER_COMMIT"
    ancestor=subprocess.run(["git","merge-base","--is-ancestor",base,current_head],cwd=ws,text=True,capture_output=True,check=False)
    if ancestor.returncode:
        return "CONSUMED_RECOVERY_NON_DESCENDANT"
    diff=subprocess.run(["git","diff","--binary",base,current_head],cwd=ws,text=True,capture_output=True,check=False)
    import hashlib
    digest=hashlib.sha256(diff.stdout.encode("utf-8",errors="replace")).hexdigest() if not diff.returncode else None
    if digest!=expected.get("diff_sha256"):
        return "CONSUMED_RECOVERY_COMMIT_MISMATCH"
    record["execution_stage"]="COMMITTED"
    record["head_sha"]=current_head
    record["recovery_state"]="RECOVERING"
    record["recovery_result"]="CONSUMED_RECOVERY_COMMIT_ADOPTED"
    cap["outcome"]="COMMITTED_EXACT_RECONCILED"
    cap["committed_head_sha"]=current_head
    return "CONSUMED_RECOVERY_COMMIT_ADOPTED"

def reconcile_consumed_staged_work(ws:Path,record:dict)->str:
    """Recognize exact staged work after consumed authority; never reuse old authority."""
    cap=record.get("preserved_recovery_capability")
    if not isinstance(cap,dict) or cap.get("state")!="CONSUMED":
        return "STAGED_RECOVERY_NOT_APPLICABLE"
    base=record.get("base_sha"); expected=record.get("preserved_work_evidence",{})
    if not base or not record.get("branch") or not expected.get("diff_sha256"):
        return "STAGED_RECOVERY_EVIDENCE_MISSING"
    head=subprocess.run(["git","rev-parse","HEAD"],cwd=ws,text=True,capture_output=True,check=False)
    branch=subprocess.run(["git","branch","--show-current"],cwd=ws,text=True,capture_output=True,check=False)
    if head.returncode or branch.returncode:
        return "STAGED_RECOVERY_WORKSPACE_UNREADABLE"
    if head.stdout.strip()!=base:
        return "STAGED_RECOVERY_HEAD_MOVED"
    if branch.stdout.strip()!=record["branch"]:
        return "STAGED_RECOVERY_BRANCH_MISMATCH"
    diff=subprocess.run(["git","diff","--binary",base],cwd=ws,text=True,capture_output=True,check=False)
    cached=subprocess.run(["git","diff","--cached","--binary",base],cwd=ws,text=True,capture_output=True,check=False)
    unstaged=subprocess.run(["git","diff","--binary"],cwd=ws,text=True,capture_output=True,check=False)
    if diff.returncode or cached.returncode or unstaged.returncode:
        return "STAGED_RECOVERY_DIFF_UNREADABLE"
    import hashlib
    full_digest=hashlib.sha256(diff.stdout.encode("utf-8",errors="replace")).hexdigest()
    cached_digest=hashlib.sha256(cached.stdout.encode("utf-8",errors="replace")).hexdigest()
    if full_digest!=expected["diff_sha256"] or cached_digest!=expected["diff_sha256"] or unstaged.stdout:
        return "STAGED_RECOVERY_NOT_EXACTLY_STAGED"
    cap["outcome"]="EXACT_STAGED_RECONCILED"
    record["recovery_state"]="QUARANTINED"
    record["recovery_result"]="CONSUMED_RECOVERY_EXACT_STAGED"
    return "CONSUMED_RECOVERY_EXACT_STAGED"

def issue_staged_commit_capability(record:dict)->dict|None:
    """Issue replacement authority for commit only after exact staged reconciliation."""
    old=record.get("preserved_recovery_capability")
    evidence=record.get("preserved_work_evidence",{})
    if record.get("recovery_result")!="CONSUMED_RECOVERY_EXACT_STAGED" or not isinstance(old,dict):
        return None
    if old.get("state")!="CONSUMED" or old.get("outcome")!="EXACT_STAGED_RECONCILED":
        return None
    if not old.get("capability_id") or not evidence.get("diff_sha256"):
        return None
    import secrets
    return {"capability_id":secrets.token_urlsafe(18),"state":"ISSUED","scope":"COMMIT_ONLY",
            "parent_capability_id":old["capability_id"],"repository":record.get("repository"),
            "base_sha":record.get("base_sha"),"branch":record.get("branch"),
            "preserved_diff_sha256":evidence["diff_sha256"],"issued_at":datetime.now(UTC).isoformat()}

def commit_reconciled_staged_work(ws:Path,record:dict)->str:
    """Consume commit-only replacement authority; never stage/reset preserved work."""
    cap=record.get("staged_commit_capability")
    if not isinstance(cap,dict) or cap.get("state")!="ISSUED" or cap.get("scope")!="COMMIT_ONLY":
        return "STAGED_COMMIT_CAPABILITY_UNAVAILABLE"
    old=record.get("preserved_recovery_capability",{}); evidence=record.get("preserved_work_evidence",{})
    bindings={"parent_capability_id":old.get("capability_id"),"repository":record.get("repository"),
              "base_sha":record.get("base_sha"),"branch":record.get("branch"),
              "preserved_diff_sha256":evidence.get("diff_sha256")}
    if any(cap.get(k)!=v for k,v in bindings.items()):
        return "STAGED_COMMIT_CAPABILITY_BINDING_MISMATCH"
    if reconcile_consumed_staged_work(ws,record)!="CONSUMED_RECOVERY_EXACT_STAGED":
        return "STAGED_COMMIT_IDENTITY_CHANGED"
    cap["state"]="CONSUMED"; cap["consumed_at"]=datetime.now(UTC).isoformat()
    commit=subprocess.run(["git","commit","-m","chore: recover validated autonomous work"],cwd=ws,text=True,capture_output=True,check=False)
    if commit.returncode:
        cap["outcome"]="COMMIT_FAILED"; return "STAGED_COMMIT_FAILED"
    head=subprocess.run(["git","rev-parse","HEAD"],cwd=ws,text=True,capture_output=True,check=False)
    branch=subprocess.run(["git","branch","--show-current"],cwd=ws,text=True,capture_output=True,check=False)
    diff=subprocess.run(["git","diff","--binary",record["base_sha"],head.stdout.strip()],cwd=ws,text=True,capture_output=True,check=False) if not head.returncode else None
    import hashlib
    digest=hashlib.sha256(diff.stdout.encode("utf-8",errors="replace")).hexdigest() if diff and not diff.returncode else None
    if head.returncode or branch.returncode or branch.stdout.strip()!=record["branch"] or digest!=evidence.get("diff_sha256"):
        cap["outcome"]="POST_COMMIT_PROOF_FAILED"; return "STAGED_COMMIT_PROOF_FAILED"
    record["execution_stage"]="COMMITTED"; record["head_sha"]=head.stdout.strip()
    record["recovery_state"]="RECOVERING"; record["recovery_result"]="STAGED_COMMIT_RECOVERED"
    cap["outcome"]="COMMITTED_EXACT"; cap["committed_head_sha"]=record["head_sha"]
    return "STAGED_COMMIT_RECOVERED"

def pending_recovery_grants(state_root:Path,newly_abandoned:list[str]|None=None)->list[str]:
    try:
        """Return every durable abandoned claim that has not reached a terminal recovery state."""
        path=state_root/"hands-off-grant-lifecycle.json"
        lifecycle_lock=_acquire_lifecycle_lock(path)
        try:
            payload=json.loads(path.read_text(encoding="utf-8"))
            grants=payload.get("grants",{})
        except (OSError,json.JSONDecodeError,TypeError):
            return list(dict.fromkeys(newly_abandoned or []))
        pending=list(newly_abandoned or [])
        changed=False
        for grant_id,record in grants.items():
            if not isinstance(record,dict) or record.get("state")!="ABANDONED":
                continue
            recovery_state=record.get("recovery_state")
            if recovery_state not in {"RECOVERED","QUARANTINED"}:
                pending.append(grant_id)
            elif (recovery_state=="QUARANTINED" and record.get("execution_stage")=="COMMITTED"
                  and record.get("head_sha") and record.get("recovery_result") in {
                      "PRESERVED_COMMIT_RECOVERED","CONSUMED_RECOVERY_COMMIT_ADOPTED","STAGED_COMMIT_RECOVERED"
                  }):
                # Exact recovered commits must re-enter durable push/PR recovery.
                record["recovery_state"]="RECOVERING"
                changed=True
                pending.append(grant_id)
        if changed:
            try:
                payload["grants"]=grants
                atomic(path,payload)
            except (OSError,TypeError):
                return list(dict.fromkeys(newly_abandoned or []))
        return list(dict.fromkeys(pending))

    finally:
        _release_lifecycle_lock(lifecycle_lock)

def verify_durable_pr(repo:str,ws:Path,pr_number:int,expected_head:str)->str:
    """Recover PR_OPEN only by exact durable PR number and exact recorded head."""
    viewed=subprocess.run(["gh","pr","view",str(pr_number),"--repo",repo,
                           "--json","number,state,headRefOid"],cwd=ws,text=True,capture_output=True,check=False)
    if viewed.returncode:
        return "RECOVERY_DURABLE_PR_UNREADABLE"
    try:
        pr=json.loads(viewed.stdout)
    except (json.JSONDecodeError,TypeError):
        return "RECOVERY_DURABLE_PR_UNREADABLE"
    if pr.get("number")!=pr_number or pr.get("headRefOid")!=expected_head:
        return "RECOVERY_DURABLE_PR_IDENTITY_MISMATCH"
    state=str(pr.get("state","")).upper()
    if state=="OPEN":
        return "RECOVERY_DURABLE_PR_HANDOFF"
    if state=="MERGED":
        return "RECOVERY_DURABLE_PR_MERGED"
    if state=="CLOSED":
        return "RECOVERY_DURABLE_PR_CLOSED_UNMERGED"
    return "RECOVERY_DURABLE_PR_STATE_UNKNOWN"

def begin_recovery_generation(path:Path,grant_id:str,expected:dict)->tuple[str,dict]|None:
    """Reserve one exact lifecycle generation before recovery may perform side effects."""
    lock=_acquire_lifecycle_lock(path)
    try:
        try:
            payload=json.loads(path.read_text(encoding="utf-8"))
            current=payload["grants"][grant_id]
        except (OSError,json.JSONDecodeError,KeyError,TypeError):
            return None
        if current!=expected:
            return None
        if current.get("recovery_generation"):
            owner=current.get("recovery_generation_worker") or current.get("recovery_owner")
            started=current.get("recovery_generation_started_at")
            stale=False
            try:
                when=datetime.fromisoformat(started)
                if when.tzinfo is None: when=when.replace(tzinfo=UTC)
                stale=(datetime.now(UTC)-when).total_seconds()>RECOVERY_GENERATION_LEASE_SECONDS
            except (TypeError,ValueError):
                stale=True
            if not stale or worker_liveness(owner)!="DEAD":
                return None
            current=dict(current)
            current.pop("recovery_generation",None)
            current.pop("recovery_generation_started_at",None)
            current.pop("recovery_owner",None)
            current.pop("recovery_generation_worker",None)
            current["recovery_generation_reclaimed_at"]=datetime.now(UTC).isoformat()
        target_repos=set(current.get("authorized_repositories",[]))
        if current.get("repository"):
            target_repos.add(current["repository"])
        for other_id,other in payload.get("grants",{}).items():
            if other_id==grant_id or not isinstance(other,dict):
                continue
            other_repos=set(other.get("authorized_repositories",[]))
            if other.get("repository"):
                other_repos.add(other["repository"])
            owns=other.get("state") in {"ISSUED","CLAIMED","QUARANTINED"} or bool(other.get("recovery_generation"))
            if owns and target_repos.intersection(other_repos):
                return None
        token=uuid.uuid4().hex
        reserved=dict(current)
        reserved["recovery_generation"]=token
        reserved["recovery_generation_started_at"]=datetime.now(UTC).isoformat()
        reserved["recovery_generation_worker"]={"host":socket.gethostname(),"pid":os.getpid(),
                                                 "process_start_fingerprint":process_start_fingerprint()}
        reserved["recovery_version"]=int(current.get("recovery_version",0))+1
        payload["grants"][grant_id]=reserved
        atomic(path,payload)
        return token,reserved
    finally:
        _release_lifecycle_lock(lock)

def commit_recovery_record(path:Path,grant_id:str,expected:dict,updated:dict)->bool:
    """Commit only the exact reserved recovery generation; compare the complete record."""
    lock=_acquire_lifecycle_lock(path)
    try:
        try:
            payload=json.loads(path.read_text(encoding="utf-8"))
            current=payload["grants"][grant_id]
        except (OSError,json.JSONDecodeError,KeyError,TypeError):
            return False
        if current!=expected:
            return False
        final=dict(updated)
        final.pop("recovery_generation",None)
        final.pop("recovery_generation_started_at",None)
        final.pop("recovery_generation_worker",None)
        final.pop("recovery_generation_unknown_checks",None)
        payload["grants"][grant_id]=final
        atomic(path,payload)
        return True
    finally:
        _release_lifecycle_lock(lock)

def reconcile_abandoned_claims(workspace_root:Path,state_root:Path,grant_ids:list[str],now:datetime|None=None)->dict[str,str]:
    """Revisit durable recovery obligations until recovered or explicitly quarantined."""
    path=state_root/"hands-off-grant-lifecycle.json"
    if not grant_ids or not path.exists():
        return {}
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
        grants=payload["grants"]
    except (OSError,json.JSONDecodeError,KeyError,TypeError):
        return {grant_id:"LIFECYCLE_UNREADABLE" for grant_id in grant_ids}
    outcomes={}
    current=now or datetime.now(UTC)
    expected_records={}
    for grant_id in grant_ids:
        original=dict(grants.get(grant_id,{}))
        reservation=begin_recovery_generation(path,grant_id,original)
        if reservation is None:
            outcomes[grant_id]="RECOVERY_GENERATION_BUSY_OR_STALE"
            continue
        _,record=reservation
        grants[grant_id]=record
        expected_records[grant_id]=dict(record)
        attempts=int(record.get("recovery_attempts",0))
        next_attempt=record.get("recovery_next_attempt_at")
        if next_attempt:
            try:
                due=datetime.fromisoformat(next_attempt)
                if due.tzinfo is None: due=due.replace(tzinfo=UTC)
                if current<due:
                    outcomes[grant_id]="RECOVERY_BACKOFF"
                    continue
            except (TypeError,ValueError):
                pass
        if attempts>=5:
            record["recovery_state"]="QUARANTINED"
            record["recovery_result"]="RECOVERY_RETRY_EXHAUSTED"
            outcomes[grant_id]="RECOVERY_RETRY_EXHAUSTED"
            continue
        record["recovery_attempts"]=attempts+1
        record["recovery_last_attempt_at"]=current.isoformat()
        repos=record.get("authorized_repositories",[])
        repo=record.get("repository")
        candidates=[repo] if repo else repos
        if len(candidates)!=1:
            outcomes[grant_id]="RECOVERY_IDENTITY_AMBIGUOUS"
        else:
            repo=candidates[0]; ws=workspace_root/repo.split("/",1)[-1]
            if not ws.is_dir():
                outcomes[grant_id]="WORKSPACE_MISSING"
            else:
                outcomes[grant_id]=None
        if outcomes.get(grant_id) is not None:
            record["recovery_result"]=outcomes[grant_id]
            delay=min(3600,60*(2**attempts))
            record["recovery_next_attempt_at"]=(current+timedelta(seconds=delay)).isoformat()
            continue
        stage=record.get("execution_stage")
        durable_branch=record.get("branch")
        durable_head=record.get("head_sha")
        durable_base=record.get("base_sha")
        if stage in {"BASE_VERIFIED","BRANCH_CREATED"} and durable_base:
            status=subprocess.run(["git","status","--porcelain"],cwd=ws,text=True,capture_output=True,check=False)
            head_now=subprocess.run(["git","rev-parse","HEAD"],cwd=ws,text=True,capture_output=True,check=False)
            branch_now=subprocess.run(["git","branch","--show-current"],cwd=ws,text=True,capture_output=True,check=False)
            if status.returncode or head_now.returncode or branch_now.returncode:
                outcomes[grant_id]="RECOVERY_EARLY_STAGE_WORKSPACE_UNREADABLE"
            elif status.stdout.strip():
                evidence=preserved_work_evidence(ws,durable_base)
                if builder_artifacts_only(evidence):
                    outcomes[grant_id]="RECOVERY_EARLY_STAGE_BUILDER_ARTIFACTS_ONLY"
                    record["recovery_state"]="RECOVERED"
                    record["recovered_at"]=current.isoformat()
                    record["ignored_builder_artifacts"]=[item["path"] for item in evidence["untracked"]]
                else:
                    outcomes[grant_id]="RECOVERY_EARLY_STAGE_DIRTY_PRESERVED"
                    record["recovery_state"]="QUARANTINED"
                    record["recovery_quarantine_reason"]="UNCOMMITTED_WORK_REQUIRES_PRESERVATION"
                    record["preserved_work_evidence"]=evidence
            elif head_now.stdout.strip()!=durable_base:
                outcomes[grant_id]="RECOVERY_EARLY_STAGE_HEAD_DRIFT"
                record["recovery_state"]="QUARANTINED"
                record["recovery_quarantine_reason"]="UNRECORDED_COMMIT_REQUIRES_PRESERVATION"
                record["preserved_work_evidence"]=preserved_work_evidence(ws,durable_base)
            elif stage=="BRANCH_CREATED" and durable_branch and branch_now.stdout.strip()!=durable_branch:
                outcomes[grant_id]="RECOVERY_EARLY_STAGE_BRANCH_DRIFT"
                record["recovery_state"]="QUARANTINED"
                record["recovery_quarantine_reason"]="BRANCH_IDENTITY_MISMATCH"
                record["preserved_work_evidence"]=preserved_work_evidence(ws,durable_base)
            else:
                outcomes[grant_id]="RECOVERY_EARLY_STAGE_NO_PRODUCT_WORK"
                record["recovery_state"]="RECOVERED"
                record["recovered_at"]=current.isoformat()
            record["recovery_result"]=outcomes[grant_id]
            if record.get("recovery_state") in {"RECOVERED","QUARANTINED"}:
                record.pop("recovery_next_attempt_at",None)
            else:
                delay=min(3600,60*(2**attempts))
                record["recovery_next_attempt_at"]=(current+timedelta(seconds=delay)).isoformat()
            continue
        if stage in {"PUSHED","PR_OPEN"} and durable_branch and durable_head:
            remote=subprocess.run(["git","ls-remote","--heads","origin",durable_branch],cwd=ws,text=True,capture_output=True,check=False)
            remote_head=remote.stdout.split()[0] if remote.returncode==0 and remote.stdout.strip() else ""
            if remote_head!=durable_head:
                outcomes[grant_id]="RECOVERY_DURABLE_REMOTE_HEAD_MISMATCH"
            elif stage=="PR_OPEN":
                pr_number=record.get("pr_number")
                if not isinstance(pr_number,int) or pr_number<1:
                    outcomes[grant_id]="RECOVERY_DURABLE_PR_IDENTITY_REQUIRED"
                else:
                    outcomes[grant_id]=verify_durable_pr(repo,ws,pr_number,durable_head)
                if outcomes[grant_id] in {"RECOVERY_DURABLE_PR_HANDOFF","RECOVERY_DURABLE_PR_MERGED"}:
                    record["recovery_state"]="RECOVERED"
                    record["recovered_at"]=current.isoformat()
                elif outcomes[grant_id]=="RECOVERY_DURABLE_PR_CLOSED_UNMERGED":
                    record["recovery_state"]="QUARANTINED"
                    record["recovery_quarantine_reason"]="PR_CLOSED_WITHOUT_MERGE"
            else:
                evidence={"head_sha":durable_head,"remote_head_sha":record.get("remote_head_sha") or durable_head}
                outcomes[grant_id]=resume_orphan_branch(repo,ws,durable_branch,"ORPHAN_PUSHED_NO_PR",evidence)
                if outcomes[grant_id]=="RECOVERY_PR_OPENED":
                    record["recovery_state"]="RECOVERED"
                    record["recovered_at"]=current.isoformat()
            record["recovery_result"]=outcomes[grant_id]
            if record.get("recovery_state")=="RECOVERED":
                record.pop("recovery_next_attempt_at",None)
            else:
                delay=min(3600,60*(2**attempts))
                record["recovery_next_attempt_at"]=(current+timedelta(seconds=delay)).isoformat()
            continue
        if stage=="COMMITTED" and durable_branch and durable_head:
            branch_now=subprocess.run(["git","branch","--show-current"],cwd=ws,text=True,capture_output=True,check=False)
            head_now=subprocess.run(["git","rev-parse","HEAD"],cwd=ws,text=True,capture_output=True,check=False)
            if branch_now.returncode or head_now.returncode or branch_now.stdout.strip()!=durable_branch or head_now.stdout.strip()!=durable_head:
                outcomes[grant_id]="RECOVERY_DURABLE_LOCAL_HEAD_MISMATCH"
            else:
                evidence={"head_sha":durable_head}
                outcomes[grant_id]=resume_orphan_branch(repo,ws,durable_branch,"ORPHAN_LOCAL_COMMIT_UNPUSHED",evidence)
                if outcomes[grant_id]=="RECOVERY_PR_OPENED":
                    record["recovery_state"]="RECOVERED"
                    record["recovered_at"]=current.isoformat()
            record["recovery_result"]=outcomes[grant_id]
            if record.get("recovery_state")=="RECOVERED":
                record.pop("recovery_next_attempt_at",None)
            else:
                delay=min(3600,60*(2**attempts))
                record["recovery_next_attempt_at"]=(current+timedelta(seconds=delay)).isoformat()
            continue
        status=subprocess.run(["git","status","--porcelain"],cwd=ws,text=True,capture_output=True,check=False)
        branch=subprocess.run(["git","branch","--show-current"],cwd=ws,text=True,capture_output=True,check=False)
        if status.returncode or branch.returncode:
            outcomes[grant_id]="WORKSPACE_UNREADABLE"; continue
        dirty=bool(status.stdout.strip())
        current_branch=branch.stdout.strip()
        if dirty:
            outcomes[grant_id]="DIRTY_WORKSPACE_QUARANTINED"
        elif current_branch.startswith("actor/autonomous-"):
            classification,evidence=classify_orphan_branch(repo,ws,current_branch)
            outcomes[grant_id]=classification
            record["recovery_evidence"]=evidence
            if classification in {"ORPHAN_LOCAL_COMMIT_UNPUSHED","ORPHAN_PUSHED_NO_PR"}:
                classification=resume_orphan_branch(repo,ws,current_branch,classification,evidence)
                outcomes[grant_id]=classification
            if classification in {"ORPHAN_NO_DIFF","ORPHAN_PR_RECOVERABLE","RECOVERY_PR_OPENED"}:
                record["recovery_state"]="RECOVERED"
                record["recovered_at"]=datetime.now(UTC).isoformat()
        else:
            outcomes[grant_id]="CLEAN_WORKSPACE_RECOVERED"
            record["recovery_state"]="RECOVERED"
            record["recovered_at"]=datetime.now(UTC).isoformat()
        record["recovery_result"]=outcomes[grant_id]
        if record.get("recovery_state") not in {"RECOVERED","QUARANTINED"}:
            delay=min(3600,60*(2**attempts))
            record["recovery_next_attempt_at"]=(current+timedelta(seconds=delay)).isoformat()
        else:
            record.pop("recovery_next_attempt_at",None)
    for grant_id in grant_ids:
        if grant_id not in grants or grant_id not in expected_records:
            continue
        if not commit_recovery_record(path,grant_id,expected_records[grant_id],grants[grant_id]):
            outcomes[grant_id]="RECOVERY_CONCURRENT_STATE_CHANGE"
    return outcomes

def recheck_preserved_work_quarantines(workspace_root:Path,state_root:Path)->dict[str,str]:
    """Continuously revalidate preserved crash work without mutating it."""
    path=state_root/"hands-off-grant-lifecycle.json"
    if not path.exists(): return {}
    try:
        payload=json.loads(path.read_text(encoding="utf-8")); grants=payload.get("grants",{})
    except (OSError,json.JSONDecodeError,TypeError):
        return {}
    outcomes={}; changed=False
    expected_records={}
    for grant_id,record in list(grants.items()):
        if not isinstance(record,dict) or record.get("recovery_state")!="QUARANTINED":
            continue
        if record.get("recovery_quarantine_reason") not in {
            "UNCOMMITTED_WORK_REQUIRES_PRESERVATION","UNRECORDED_COMMIT_REQUIRES_PRESERVATION","BRANCH_IDENTITY_MISMATCH"
        }: continue
        original=dict(record)
        reservation=begin_recovery_generation(path,grant_id,original)
        if reservation is None:
            outcomes[grant_id]="PRESERVED_RECOVERY_GENERATION_BUSY_OR_STALE"
            continue
        _,record=reservation
        grants[grant_id]=record
        expected_records[grant_id]=dict(record)
        repo=record.get("repository")
        if not repo:
            outcomes[grant_id]="PRESERVED_REPOSITORY_MISSING"
            record["preserved_work_revalidation"]="PRESERVED_REPOSITORY_MISSING"
            changed=True
            continue
        ws=workspace_root/repo.split("/",1)[-1]
        if ws.is_dir() and record.get("preserved_recovery_capability",{}).get("state")=="CONSUMED":
            reconciled=reconcile_consumed_recovery(ws,record)
            if reconciled in {"CONSUMED_RECOVERY_COMMIT_ADOPTED","CONSUMED_RECOVERY_ALREADY_DURABLE"}:
                outcomes[grant_id]=reconciled
                changed=True
                continue
            if reconciled=="CONSUMED_RECOVERY_NO_COMMIT":
                staged=reconcile_consumed_staged_work(ws,record)
                if staged=="CONSUMED_RECOVERY_EXACT_STAGED":
                    if not record.get("staged_commit_capability"):
                        replacement=issue_staged_commit_capability(record)
                        if replacement: record["staged_commit_capability"]=replacement
                    if record.get("staged_commit_capability",{}).get("state")=="ISSUED":
                        staged=commit_reconciled_staged_work(ws,record)
                    outcomes[grant_id]=staged
                    changed=True
                    continue
        outcome=revalidate_preserved_work(ws,record) if ws.is_dir() else "PRESERVED_WORKSPACE_MISSING"
        outcomes[grant_id]=outcome
        record["preserved_work_revalidation"]=outcome
        record["preserved_work_revalidated_at"]=datetime.now(UTC).isoformat()
        if outcome=="PRESERVED_WORK_EXACT":
            evidence=record.get("preserved_work_evidence",{})
            if builder_artifacts_only(evidence):
                outcomes[grant_id]="PRESERVED_BUILDER_ARTIFACTS_RELEASED"
                record["preserved_work_revalidation"]=outcomes[grant_id]
                record["recovery_state"]="RECOVERED"
                record["recovered_at"]=datetime.now(UTC).isoformat()
                record["ignored_builder_artifacts"]=[item["path"] for item in evidence["untracked"]]
                changed=True
                continue
            validation=validate_preserved_work(ws,record)
            record["preserved_work_validation"]=validation
            record["preserved_work_validated_at"]=datetime.now(UTC).isoformat()
            if validation=="PRESERVED_VALIDATION_PASSED" and not record.get("preserved_recovery_capability"):
                capability=issue_preserved_recovery_capability(record)
                if capability:
                    record["preserved_recovery_capability"]=capability
            if validation=="PRESERVED_VALIDATION_PASSED" and record.get("preserved_recovery_capability",{}).get("state")=="ISSUED":
                commit_outcome=commit_preserved_work(ws,record)
                record["preserved_work_commit_result"]=commit_outcome
                outcomes[grant_id]=commit_outcome
            else:
                outcomes[grant_id]=validation
        changed=True
    if changed:
        for grant_id in list(outcomes):
            record=grants.get(grant_id)
            if isinstance(record,dict) and record!=expected_records.get(grant_id):
                if not commit_recovery_record(path,grant_id,expected_records.get(grant_id,{}),record):
                    outcomes[grant_id]="PRESERVED_RECOVERY_CONCURRENT_STATE_CHANGE"
    return outcomes

def recheck_quarantined_claims(state_root:Path,now:datetime|None=None,liveness_probe=worker_liveness)->dict[str,str]:
    try:
        """Re-evaluate ownership quarantine; only proof of death authorizes recovery."""
        path=state_root/"hands-off-grant-lifecycle.json"
        lifecycle_lock=_acquire_lifecycle_lock(path)
        if not path.exists():
            return {}
        try:
            payload=json.loads(path.read_text(encoding="utf-8")); grants=payload.get("grants",{})
        except (OSError,json.JSONDecodeError,TypeError):
            return {}
        current=now or datetime.now(UTC); outcomes={}; changed=False
        for grant_id,record in grants.items():
            if not isinstance(record,dict) or record.get("state")!="QUARANTINED":
                continue
            result_status=record.get("result_status")
            if result_status not in {"CLAIM_LEASE_UNKNOWN_QUARANTINED","ISSUE_LEASE_UNKNOWN_QUARANTINED"}:
                continue
            if record.get("recovery_generation"):
                outcomes[grant_id]="RECOVERY_GENERATION_ACTIVE"
                continue
            owner_field="issuer" if result_status=="ISSUE_LEASE_UNKNOWN_QUARANTINED" else "worker"
            liveness=liveness_probe(record.get(owner_field))
            record["quarantine_last_recheck_at"]=current.isoformat()
            record["quarantine_rechecks"]=int(record.get("quarantine_rechecks",0))+1
            record["worker_liveness"]=liveness; changed=True
            if liveness=="DEAD":
                record["state"]="ABANDONED"
                record["result_status"]=(
                    "ISSUE_LEASE_QUARANTINE_ISSUER_DEAD"
                    if owner_field=="issuer" else "CLAIM_LEASE_QUARANTINE_WORKER_DEAD"
                )
                record["finished_at"]=current.isoformat()
                outcomes[grant_id]="RELEASED_TO_RECOVERY"
            elif liveness=="ALIVE":
                record["state"]="ISSUED" if owner_field=="issuer" else "CLAIMED"
                record["result_status"]=(
                    "ISSUE_LEASE_QUARANTINE_ISSUER_ALIVE"
                    if owner_field=="issuer" else "CLAIM_LEASE_QUARANTINE_WORKER_ALIVE"
                )
                counter="issue_unknown_liveness_checks" if owner_field=="issuer" else "unknown_liveness_checks"
                record[counter]=0
                record.pop("unknown_liveness_first_seen_at",None)
                record.pop("finished_at",None)
                outcomes[grant_id]="RESTORED_TO_ISSUED" if owner_field=="issuer" else "RESTORED_TO_CLAIMED"
            else:
                outcomes[grant_id]="STILL_UNKNOWN"
        if changed: atomic(path,payload)
        return outcomes

    finally:
        _release_lifecycle_lock(lifecycle_lock)

def quarantined_grants(state_root:Path)->dict[str,dict]:
    """Expose terminal ownership/recovery quarantines without leaking worker secrets."""
    path=state_root/"hands-off-grant-lifecycle.json"
    if not path.exists():
        return {}
    try:
        grants=json.loads(path.read_text(encoding="utf-8")).get("grants",{})
    except (OSError,json.JSONDecodeError,TypeError):
        return {}
    result={}
    for grant_id,record in grants.items():
        if not isinstance(record,dict):
            continue
        if record.get("state")=="QUARANTINED" or record.get("recovery_state")=="QUARANTINED":
            worker=record.get("worker") if isinstance(record.get("worker"),dict) else {}
            result[grant_id]={
                "repository":record.get("repository"),
                "result_status":record.get("result_status"),
                "recovery_result":record.get("recovery_result"),
                "unknown_liveness_checks":record.get("unknown_liveness_checks",0),
                "worker_host":worker.get("host"),
                "worker_pid":worker.get("pid"),
                "process_start_fingerprint":worker.get("process_start_fingerprint"),
                "finished_at":record.get("finished_at"),
            }
    return result

def productive_liveness(state_root:Path,authorized_products:list[str],now:datetime|None=None,stale_seconds:int=900,
                        builder_status:str|None=None)->dict:
    """Expose recent productive flow without confusing authorization with executable work."""
    current=now or datetime.now(UTC)
    lifecycle=state_root/"hands-off-grant-lifecycle.json"
    try:
        grants=json.loads(lifecycle.read_text(encoding="utf-8")).get("grants",{})
    except (OSError,json.JSONDecodeError,TypeError):
        grants={}
    records=[]; latest=None; active=False
    for grant_id,record in grants.items():
        if not isinstance(record,dict): continue
        state=record.get("state")
        if state not in {"CLAIMED","RUNNING","COMPLETED"}: continue
        stamp=(record.get("last_productive_mutation_at") or record.get("stage_updated_at")
               or record.get("last_heartbeat_at") or record.get("finished_at")
               or record.get("claimed_at") or record.get("started_at"))
        parsed=None
        if isinstance(stamp,str):
            try: parsed=datetime.fromisoformat(stamp.replace("Z","+00:00"))
            except ValueError: parsed=None
        if parsed and (latest is None or parsed>latest): latest=parsed
        is_active=state in {"CLAIMED","RUNNING"}
        active=active or is_active
        records.append({"grant_id":grant_id,"repository":record.get("repository"),
                        "slice_id":record.get("slice_id"),"head_sha":record.get("head_sha") or record.get("exact_head"),
                        "stage":record.get("execution_stage") or record.get("stage") or state,
                        "state":state,"progress_at":stamp})
    age=(current-latest).total_seconds() if latest else None
    terminal=builder_status=="NO_WORK"
    eligible=bool(authorized_products) and not terminal
    productive=bool(records) and age is not None and age<=stale_seconds
    if terminal or not authorized_products: status="NO_AUTHORIZED_WORK"
    elif productive: status="PRODUCTIVE"
    elif active: status="STALE_ACTIVE_WORK"
    else: status="ELIGIBLE_IDLE"
    return {"status":status,"productive":productive,"eligible_work":eligible,
            "authorized_products":sorted(authorized_products),"grant_evidence":records,
            "last_productive_mutation_at":latest.isoformat() if latest else None,
            "seconds_since_productive_mutation":age,"stale_after_seconds":stale_seconds}

def product_convergence(state_root:Path,authorized_products:list[str],*,current_invocation_id:str|None=None)->dict:
    """Prove each authorized product is owned or has fresh, explicit non-execution evidence."""
    authorized={p for p in authorized_products if p in {"knowledge-engine","rocksmith","everward"}}
    active={}
    try:
        grants=json.loads((state_root/"hands-off-grant-lifecycle.json").read_text(encoding="utf-8")).get("grants",{})
    except (OSError,json.JSONDecodeError,TypeError):
        grants={}
    for grant_id,record in grants.items():
        if not isinstance(record,dict) or record.get("state") not in {"ISSUED","CLAIMED","RUNNING"}:
            continue
        product=product_for_repository(record.get("repository"))
        if product in authorized:
            active[product]={"grant_id":grant_id,"repository":record.get("repository"),"state":record.get("state")}
    reasons={}
    try:
        decision=json.loads((state_root/"dispatch-decision.json").read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError,TypeError):
        decision={}
    decision_is_fresh=(isinstance(decision,dict)
        and (current_invocation_id is None or decision.get("invocation_id")==current_invocation_id))
    for item in decision.get("decisions",[]) if decision_is_fresh else []:
        if not isinstance(item,dict): continue
        product=item.get("product"); outcome=item.get("outcome")
        if product in authorized and product not in active and outcome not in {None,"SELECTED"}:
            reasons.setdefault(product,[]).append({"outcome":outcome,"reason":item.get("reason"),
                                                   "repository":item.get("repository")})
    missing=sorted(authorized-set(active)-set(reasons))
    status="CONVERGED" if not missing else "UNEXPLAINED_ELIGIBLE_IDLE"
    payload={"schema_version":1,"status":status,"authorized_products":sorted(authorized),
             "active_products":active,"explained_nonexecuting_products":reasons,
             "dispatch_evidence_fresh":decision_is_fresh,
             "dispatch_invocation_id":decision.get("invocation_id") if isinstance(decision,dict) else None,
             "unexplained_eligible_products":missing,"recorded_at_utc":datetime.now(UTC).isoformat()}
    atomic(state_root/"product-convergence.json",payload)
    return payload

def request_liveness_reacquire(state_root:Path,liveness:dict)->dict:
    """Turn a proven idle/stale condition into a bounded immediate scheduler request."""
    status=liveness.get("status")
    if status not in {"ELIGIBLE_IDLE","STALE_ACTIVE_WORK"}:
        return {"requested":False,"reason":"LIVENESS_RECOVERY_NOT_REQUIRED"}
    # Never steal an active claim here. Stale ownership is handled by the
    # lease/liveness recovery path; this only requests another scheduler pass.
    request_immediate_builder(state_root)
    result={"requested":True,"reason":status,
            "requested_at_utc":datetime.now(UTC).isoformat(),
            "active_claim_present":any(r.get("state") in {"CLAIMED","RUNNING"}
                for r in liveness.get("grant_evidence",[]) if isinstance(r,dict))}
    atomic(state_root/"liveness-reacquire.json",result)
    return result

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
    atomic(path,payload)
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
    atomic(path,proof)
    return proof

PRODUCT_BY_REPOSITORY={
    "jweter/knowledge-engine-core":"knowledge-engine","jweter/knowledge-engine-web":"knowledge-engine",
    "jweter/knowledge-engine-ai":"knowledge-engine","jweter/rocksmith-cdlc-generator":"rocksmith",
    "jweter/Project-Everward":"everward",
}

def product_for_repository(repository:str|None)->str|None:
    return PRODUCT_BY_REPOSITORY.get(repository) if repository else None

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
    products=sorted({e.get("product") for e in events if isinstance(e,dict) and e.get("advanced") is True and e.get("product") in {"knowledge-engine","rocksmith","everward"}})
    proof={"schema_version":1,"burst_id":burst_id,"advanced_products":products,"advanced_product_count":len(products),
           "all_three_products_proven":len(products)==3,"events":events[-100:]}
    atomic(path,proof); return proof

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
            atomic(journal_path,committed)
            return {"status":"RECOVERED_COMMITTED","sequence":journal.get("sequence")}
        aborted=dict(journal)
        aborted["status"]="ABORTED"
        aborted["aborted_at_utc"]=datetime.now(UTC).isoformat()
        atomic(journal_path,aborted)
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
    atomic(journal,{"schema_version":1,"status":"PREPARED","sequence":sequence,
                    "record_hash":record["record_hash"],"anchor_hash":payload["anchor_hash"],
                    "prepared_at_utc":datetime.now(UTC).isoformat()})
    atomic(path,payload)
    atomic(journal,{"schema_version":1,"status":"COMMITTED","sequence":sequence,
                    "record_hash":record["record_hash"],"anchor_hash":payload["anchor_hash"],
                    "committed_at_utc":datetime.now(UTC).isoformat()})
    return record

def prove_restart_takeover(state_root:Path,instance_lock:dict,burst:dict)->dict:
    """Record why this physical supervisor may continue a prior logical burst."""
    current=instance_lock["worker"]
    previous_id=burst.get("previous_process_instance_id")
    proof={"schema_version":1,"burst_id":burst["burst_id"],
           "process_instance_id":current["instance_id"],
           "previous_process_instance_id":previous_id,
           "resumed":bool(burst.get("resumed")),
           "proven_at_utc":datetime.now(UTC).isoformat()}
    if not burst.get("resumed"):
        proof["status"]="NEW_BURST_NO_TAKEOVER"
        return proof
    if not previous_id:
        proof["status"]="RESTART_PROVEN_BY_EXCLUSIVE_KERNEL_LOCK"
        proof["previous_owner_evidence"]="LEGACY_IDENTITY_UNAVAILABLE"
        return proof
    if previous_id==current["instance_id"]:
        proof["status"]="RESTART_IDENTITY_COLLISION"
        return proof
    # Acquiring the non-shareable kernel lock is the authoritative proof that
    # no prior supervisor still owns this state root. Preserve the predecessor
    # identity so restart provenance is explicit rather than inferred.
    proof["status"]="RESTART_PROVEN_BY_EXCLUSIVE_KERNEL_LOCK"
    proof["previous_owner_evidence"]="KERNEL_LOCK_RELEASED_OR_PROCESS_EXITED"
    return proof

def start_or_resume_burst(state_root:Path,*,process_instance_id:str|None=None,now:datetime|None=None,resume_seconds:int=900)->dict:
    """Resume a recently interrupted immediate burst; otherwise create a new wake identity."""
    now=now or datetime.now(UTC)
    path=state_root/"continuation-current-burst.json"
    try:
        prior=json.loads(path.read_text(encoding="utf-8"))
        updated=datetime.fromisoformat(str(prior.get("updated_at_utc","")).replace("Z","+00:00"))
        age=(now-updated).total_seconds()
        schema=prior.get("schema_version")
        burst_id=prior.get("burst_id")
        pass_count=prior.get("pass_count")
        prior_instance=prior.get("process_instance_id")
        valid_schema=schema in {1,2}
        valid_burst=isinstance(burst_id,str) and 1 <= len(burst_id) <= 128
        valid_pass=isinstance(pass_count,int) and not isinstance(pass_count,bool) and 0 <= pass_count <= 1000000
        valid_instance=(schema==1 or (isinstance(prior_instance,str) and 1 <= len(prior_instance) <= 128))
        if (prior.get("immediate_continuation") is True and valid_schema and valid_burst
                and valid_pass and valid_instance and 0 <= age <= resume_seconds):
            return {"burst_id":burst_id,"burst_pass":pass_count,
                    "resumed":True,"resume_age_seconds":age,
                    "previous_process_instance_id":prior_instance,
                    "process_instance_id":process_instance_id}
    except (OSError,json.JSONDecodeError,TypeError,ValueError):
        pass
    return {"burst_id":now.strftime("%Y%m%dT%H%M%S.%fZ"),"burst_pass":0,
            "resumed":False,"resume_age_seconds":None,"previous_process_instance_id":None,
            "process_instance_id":process_instance_id}

def main()->int:
    p=argparse.ArgumentParser()
    p.add_argument("--repo-root",required=True); p.add_argument("--workspace-root",required=True)
    p.add_argument("--state-root",required=True); p.add_argument("--builder-seconds",type=int,default=300)
    p.add_argument("--merge-seconds",type=int,default=60); p.add_argument("--once",action="store_true")
    p.add_argument("--yield-on-interactive-session",action="store_true",
                   help="SYSTEM cold-boot owner exits when an interactive user session appears")
    a=p.parse_args(); repo=Path(a.repo_root); workspace=Path(a.workspace_root); state=Path(a.state_root)
    state.mkdir(parents=True,exist_ok=True)
    instance_lock=acquire_supervisor_instance_lock(state)
    if instance_lock is None:
        owner=None
        try:
            owner=json.loads((state/"supervisor-instance.lock.json").read_text(encoding="utf-8"))
        except (OSError,json.JSONDecodeError,TypeError):
            pass
        atomic(state/"supervisor-reentry.json",{"schema_version":2,
            "status":"SUPERVISOR_INSTANCE_ALREADY_ACTIVE",
            "observed_at_utc":datetime.now(UTC).isoformat(),
            "active_process_instance_id":((owner or {}).get("worker") or {}).get("instance_id"),
            "active_worker":(owner or {}).get("worker")})
        return 0
    atexit.register(release_supervisor_instance_lock,instance_lock)
    merge=None
    def cleanup_merge_watcher()->None:
        if merge is not None and merge.poll() is None:
            merge.terminate()
            try:
                merge.wait(timeout=5)
            except subprocess.TimeoutExpired:
                merge.kill()
    atexit.register(cleanup_merge_watcher)
    process_instance_id=instance_lock["worker"]["instance_id"]
    burst=start_or_resume_burst(state,process_instance_id=process_instance_id)
    takeover_proof=prove_restart_takeover(state,instance_lock,burst)
    if takeover_proof["status"]=="RESTART_IDENTITY_COLLISION":
        release_supervisor_instance_lock(instance_lock)
        return 2
    atomic(state/"supervisor-restart-takeover.json",takeover_proof)
    atomic(state/"supervisor-process-instance.json",{"schema_version":1,
        "process_instance_id":process_instance_id,"burst_id":burst["burst_id"],
        "resumed":burst["resumed"],"previous_process_instance_id":burst.get("previous_process_instance_id"),
        "takeover_status":takeover_proof["status"],
        "worker":instance_lock["worker"],"started_at_utc":datetime.now(UTC).isoformat()})
    burst_id=burst["burst_id"]
    burst_pass=burst["burst_pass"]
    try: burst_proof=json.loads((state/"autonomous-burst-proof.json").read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError,TypeError,ValueError): burst_proof={}
    try: product_proof=json.loads((state/"portfolio-product-advancement-proof.json").read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError,TypeError,ValueError): product_proof={}
    previous_repository=None
    previous_builder_status=None
    rotation_evidence=None
    worker_pool=ParallelWorkerPool(max_workers_per_product=2,max_total_workers=6)
    atexit.register(worker_pool.shutdown,wait=False)
    while True:
        if a.yield_on_interactive_session:
            try:
                sessions=active_interactive_sessions()
                should_yield=boot_owner_should_yield(sessions)
                session_status="INTERACTIVE_SESSION_PRESENT" if should_yield else "NO_INTERACTIVE_SESSION"
            except OSError as exc:
                # Fail closed: a SYSTEM owner that cannot prove session absence
                # must not retain mutation authority.
                sessions=[]
                should_yield=True
                session_status=f"SESSION_DETECTION_FAILED:{exc}"
            atomic(state/"supervisor-session-handoff.json",{
                "schema_version":1,"status":session_status,"sessions":sessions,
                "process_instance_id":process_instance_id,
                "observed_at_utc":datetime.now(UTC).isoformat()})
            if should_yield:
                if merge is not None and merge.poll() is None:
                    merge.terminate()
                return 0
        burst_pass+=1
        if merge is None or merge.poll() is not None:
            merge=start_merge_watcher(repo,state,a.merge_seconds)
        linear_rc=run_linear_reconciliation(repo,state)
        released_recovery_generations=recover_stale_recovery_generations(state)
        abandoned_grants=recover_stale_grant_claims(state)
        recovery_queue=pending_recovery_grants(state,abandoned_grants)
        recovery_results=reconcile_abandoned_claims(workspace,state,recovery_queue)
        preserved_work_rechecks=recheck_preserved_work_quarantines(workspace,state)
        quarantine_rechecks=recheck_quarantined_claims(state)
        newly_released=[g for g,outcome in quarantine_rechecks.items() if outcome=="RELEASED_TO_RECOVERY"]
        if newly_released:
            recovery_results.update(reconcile_abandoned_claims(workspace,state,newly_released))
        quarantines=quarantined_grants(state)
        publish_direction_outbox(state)
        authorized,activation_reason,products,stale_products=evolution_authorized(repo,workspace,state)
        if stale_products:
            for product in stale_products:
                recertify_product(repo,workspace,state,product)
            authorized,activation_reason,products,stale_products=evolution_authorized(repo,workspace,state)
        if not authorized and (activation_reason=="ACTIVATION_EVIDENCE_MISSING" or stale_products):
            refreshed,refresh_reason=refresh_readiness(repo,workspace,state)
            if refreshed:
                authorized,activation_reason,products,stale_products=evolution_authorized(repo,workspace,state)
            else:
                activation_reason=refresh_reason
        products=products if authorized else []
        parallel_results=harvest_parallel_builders(worker_pool,state)
        dispatched_workers=dispatch_parallel_builders(worker_pool,repo,workspace,state,products) if products else []
        # A completed nonproductive worker has already released its slot. Refill
        # immediately in the same burst pass instead of waiting for the next loop.
        # This turns ownership/no-work/exception outcomes into routing feedback.
        if products and parallel_results and any(not row.get("healthy",False) for row in parallel_results.values()):
            dispatched_workers.extend(
                dispatch_parallel_builders(worker_pool,repo,workspace,state,products)
            )
        pool_snapshot=worker_pool.snapshot()
        # The supervisor no longer blocks on implementation. A worker holding a
        # repository grant is active work; other product slots remain schedulable.
        rc=0 if (dispatched_workers or pool_snapshot["running_total"] or parallel_results) else 3
        builder_invocation_id=None
        if parallel_results:
            statuses=[row["status"] for row in parallel_results.values()]
            builder_status=statuses[-1]
            builder_ok=all(row.get("healthy",False) for row in parallel_results.values())
        elif pool_snapshot["running_total"]:
            builder_status="PARALLEL_WORKERS_ACTIVE"
            builder_ok=True
        else:
            builder_status="NO_READY_EXECUTION_SLICE"
            builder_ok=False
        current_repository=selected_builder_repository(state,builder_invocation_id)
        rotation_evidence=maybe_record_async_rotation(state,previous_repository=previous_repository,
            previous_status=previous_builder_status,current_repository=current_repository,
            burst_id=burst_id,process_instance_id=process_instance_id)
        previous_repository=current_repository
        previous_builder_status=builder_status
        lifecycle_advanced=merge_watcher_advanced(state)
        decision_learning=refresh_decision_learning(state)
        if lifecycle_advanced:
            request_immediate_builder(state)
        liveness=productive_liveness(state,products,builder_status=builder_status)
        atomic(state/"productive-liveness.json",liveness)
        convergence=product_convergence(state,products,current_invocation_id=builder_invocation_id)
        liveness_reacquire=request_liveness_reacquire(state,liveness)
        if convergence["status"]=="UNEXPLAINED_ELIGIBLE_IDLE":
            request_immediate_builder(state)
            liveness_reacquire={"requested":True,"reason":"PRODUCT_CONVERGENCE_DEFECT",
                "products":convergence["unexplained_eligible_products"],
                "requested_at_utc":datetime.now(UTC).isoformat()}
        healthy=(builder_ok and linear_rc in (None,0) and not quarantines
                 and liveness["status"] not in {"STALE_ACTIVE_WORK","ELIGIBLE_IDLE"}
                 and convergence["status"]=="CONVERGED")
        atomic(state/"supervisor.json",{"updated_at_utc":datetime.now(UTC).isoformat(),
            "status":"HEALTHY" if healthy else "FLOW_DEGRADED","builder_returncode":rc,
            "builder_status":builder_status,
            "linear_reconciliation_returncode":linear_rc,
            "evolution_authorized":authorized,"activation_reason":activation_reason,
            "authorized_products":products,
            "parallel_worker_pool":pool_snapshot,
            "parallel_workers_dispatched":dispatched_workers,
            "parallel_worker_results":parallel_results,
            "productive_liveness":liveness,
            "product_convergence":convergence,
            "liveness_reacquire":liveness_reacquire,
            "abandoned_grants":abandoned_grants,
            "recovery_queue":recovery_queue,
            "abandoned_recovery":recovery_results,
            "preserved_work_rechecks":preserved_work_rechecks,
            "quarantine_rechecks":quarantine_rechecks,
            "quarantined_grants":quarantines,
            "quarantine_count":len(quarantines),
            "pr_lifecycle_advanced":lifecycle_advanced,
            "merge_watcher_pid":merge.pid,"merge_watcher_running":merge.poll() is None,
            "process_instance_id":process_instance_id,"burst_id":burst_id,
            "autonomous_burst_proof":burst_proof,
            "portfolio_product_advancement_proof":product_proof,
            "async_gate_rotation":rotation_evidence,
            "decision_learning":decision_learning})
        # Presentation-only persona output. Never feeds control decisions.
        (state/"manager-voice.txt").write_text(
            manager_status(healthy,liveness["status"],builder_status)+"\n",encoding="utf-8")
        # Persona diagnostics must use the supervisor-validated status. Details
        # from builder.json are accepted only for this exact invocation.
        builder_payload={"status":builder_status,"reason":""}
        try:
            raw_builder=json.loads((state/"builder.json").read_text(encoding="utf-8"))
            if raw_builder.get("invocation_id")==builder_invocation_id:
                builder_payload["reason"]=str(raw_builder.get("reason",""))
        except (OSError,json.JSONDecodeError,TypeError):
            pass
        worker_detail=builder_payload["reason"]
        (state/"worker-voice.txt").write_text(
            worker_status(builder_status,worker_detail)+"\n",encoding="utf-8")
        buddy=diagnose_blocker(builder_payload,liveness)
        atomic(state/"blocker-buddy.json",buddy)
        (state/"buddy-voice.txt").write_text(buddy["voice"]+"\n",encoding="utf-8")
        if a.once:
            merge.terminate(); return 0 if rc==0 else 1
        # A merge exposes new roadmap work. Do not impose the normal idle delay
        # after lifecycle advancement; re-enter selection immediately.
        delay=continuation_delay(lifecycle_advanced=lifecycle_advanced,
            liveness_reacquire=liveness_reacquire,builder_ok=builder_ok,
            builder_status=builder_status,normal_seconds=a.builder_seconds)
        record=record_continuation_pass(state,builder_status=builder_status,delay_seconds=delay,
            lifecycle_advanced=lifecycle_advanced,liveness_reacquire=liveness_reacquire,burst_id=burst_id,
            process_instance_id=process_instance_id)
        burst_proof=record_burst_repository_pass(state,repository=selected_builder_repository(state,builder_invocation_id),
            builder_status=builder_status,burst_id=burst_id,process_instance_id=process_instance_id,
            sequence=record["sequence"])
        product_proof=record_product_identity_advancement(state,repository=selected_builder_repository(state,builder_invocation_id),
            builder_status=builder_status,lifecycle_advanced=lifecycle_advanced,burst_id=burst_id,
            process_instance_id=process_instance_id,sequence=record["sequence"])
        record["burst_pass"]=burst_pass
        atomic(state/"continuation-current-burst.json",{"schema_version":2,"burst_id":burst_id,
            "process_instance_id":process_instance_id,
            "previous_process_instance_id":burst.get("previous_process_instance_id"),
            "resumed":burst["resumed"],"pass_count":burst_pass,"last_sequence":record["sequence"],"last_builder_status":builder_status,
            "immediate_continuation":record["immediate_continuation"],"updated_at_utc":datetime.now(UTC).isoformat()})
        time.sleep(delay)
if __name__=="__main__": raise SystemExit(main())

