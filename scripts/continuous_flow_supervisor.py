"""Current-user continuous engineering supervisor; no admin Task Scheduler required."""
from __future__ import annotations
import argparse, atexit, hashlib, json, os, shutil, socket, subprocess, sys, time, uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from project_orchestrator.grant_lifecycle import (
    acquire_lifecycle_lock as _acquire_lifecycle_lock,
    mutate_grant_lifecycle,
    release_lifecycle_lock as _release_lifecycle_lock,
)
from project_orchestrator.state_io import atomic_json as atomic

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
from project_orchestrator.execution_runtime import require_runtime_capabilities, DEFAULT_EXECUTION_RUNTIMES
from project_orchestrator.runtime_leases import RuntimeLeasePool, DEFAULT_RUNTIME_CAPACITIES
from project_orchestrator.grant_issuance import (
    acquire_grant_issue_lock as _acquire_grant_issue_lock,
    conflicting_nonterminal_grant as _conflicting_nonterminal_grant,
    finalize_unclaimed_grant,
    release_grant_issue_lock,
    reserve_execution_grant,
)
from project_orchestrator import recovery_manager as _recovery
from project_orchestrator import planning_dispatch as _planning
from project_orchestrator import parallel_runtime as _parallel_runtime


RUNTIME_LEASE_STATE=Path(os.environ.get("EXPONENTIAL_ENGINE_STATE",Path.home()/".exponential-engine"/"state"))/"runtime-leases.json"

def load_runtime_leases_with_recovery(state_path:Path=RUNTIME_LEASE_STATE)->tuple[RuntimeLeasePool,dict|None]:
    """Self-heal invalid lease authority once, but never displace a provable live owner."""
    try:
        return RuntimeLeasePool(DEFAULT_RUNTIME_CAPACITIES,state_path),None
    except RuntimeError as exc:
        if str(exc)!="RUNTIME_LEASE_STATE_INVALID":
            raise
    recovery=RuntimeLeasePool.recover_invalid_state(DEFAULT_RUNTIME_CAPACITIES,state_path)
    pool=RuntimeLeasePool(DEFAULT_RUNTIME_CAPACITIES,state_path)
    return pool,recovery

RUNTIME_LEASES,RUNTIME_LEASE_RECOVERY=load_runtime_leases_with_recovery()

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
    def mark_verified_merge(item):
        if item.get("head_sha")!=head or item.get("pr_number")!=pr:
            return False
        item["outcome_quality"]="MERGED_VERIFIED"
        item["merged_verified_at"]=datetime.now(UTC).isoformat()
        return True
    mutate_grant_lifecycle(lifecycle,grant_id,mark_verified_merge)
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
    lifecycle=state_root/"hands-off-grant-lifecycle.json"
    try:
        grants=json.loads(lifecycle.read_text(encoding="utf-8")).get("grants",{})
    except (OSError,json.JSONDecodeError,TypeError):
        grants={}
    matches=[gid for gid,item in grants.items() if isinstance(item,dict)
             and item.get("repository")==repo and item.get("head_sha")==exact_head]
    if len(matches)==1:
        def mark_product_reality(item):
            if item.get("repository")!=repo or item.get("head_sha")!=exact_head:
                return False
            item["outcome_quality"]="PRODUCT_REALITY_VERIFIED"
            item["product_reality_verified_at"]=datetime.now(UTC).isoformat()
            return True
        mutate_grant_lifecycle(lifecycle,matches[0],mark_product_reality)
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
    return _planning.reserved_quarantine_repositories(state_root)


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
    return _planning.decision_learning_hint(state_root,decision_class)



def select_execution_target(state_root:Path,activation:dict,products:list[str])->tuple[str,str]:
    return _planning.select_execution_target(
        state_root,activation,products,
        load_execution_slice_fn=load_execution_slice,
        conflict_check_fn=conflicting_nonterminal_grant,
    )


ISSUE_LEASE_SECONDS=_recovery.ISSUE_LEASE_SECONDS
RECOVERY_GENERATION_LEASE_SECONDS=_recovery.RECOVERY_GENERATION_LEASE_SECONDS
CLAIM_LEASE_SECONDS=_recovery.CLAIM_LEASE_SECONDS
UNKNOWN_LIVENESS_MAX_CHECKS=_recovery.UNKNOWN_LIVENESS_MAX_CHECKS

def process_start_fingerprint(pid:int|None=None)->str|None:
    return _recovery.process_start_fingerprint(pid)

def worker_liveness(worker:dict|None)->str:
    return _recovery.worker_liveness(worker)

def recover_stale_issued_grants(state_root:Path,now:datetime|None=None)->list[str]:
    return _recovery.recover_stale_issued_grants(state_root,now,worker_liveness)

def recover_stale_recovery_generations(state_root:Path,now:datetime|None=None,liveness_probe=None)->list[str]:
    return _recovery.recover_stale_recovery_generations(
        state_root,now,liveness_probe or worker_liveness,
    )

def recover_stale_grant_claims(state_root:Path,now:datetime|None=None,liveness_probe=None)->list[str]:
    return _recovery.recover_stale_grant_claims(
        state_root,now,liveness_probe or worker_liveness,
    )

def recheck_quarantined_claims(state_root:Path,now:datetime|None=None,liveness_probe=None)->dict[str,str]:
    return _recovery.recheck_quarantined_claims(
        state_root,now,liveness_probe or worker_liveness,
    )

def quarantined_grants(state_root:Path)->dict[str,dict]:
    return _recovery.quarantined_grants(state_root)

def slice_paths_conflict(left:dict,right:dict)->bool:
    return _planning.slice_paths_conflict(left,right)


def select_conflict_free_slices(slices:list[dict],limit:int|None=None)->list[dict]:
    return _planning.select_conflict_free_slices(slices,limit)


def task_outcome_priority(state_root:Path,task:dict)->tuple[float,int,str]:
    return _planning.task_outcome_priority(state_root,task)


def planner_decision_id(product:str,repo:str,head:str,task:dict)->str:
    return _planning.planner_decision_id(product,repo,head,task)


def persist_planner_decision(state_root:Path,product:str,candidates:list,selected:list)->None:
    _planning.persist_planner_decision(state_root,product,candidates,selected)


def ready_task_batch(state_root:Path,activation:dict,product:str,limit:int)->list[tuple[str,str,dict]]:
    return _planning.ready_task_batch(
        state_root,activation,product,limit,
        derive_slices_fn=derive_slices,
        conflict_check_fn=conflicting_nonterminal_grant,
    )


def conflicting_nonterminal_grant(state_root:Path,repo:str,execution_slice:dict)->dict|None:
    return _conflicting_nonterminal_grant(state_root,repo,execution_slice,slice_paths_conflict)

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
    return _acquire_grant_issue_lock(state_root,process_start_fingerprint())

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
        planner_id=None
        if planned_target is not None and len(selected_products)==1:
            planner_id=planner_decision_id(next(iter(selected_products)),repo,exact_head,execution_slice)
        conflict=conflicting_nonterminal_grant(state_root,repo,execution_slice)
        if conflict:
            raise RuntimeError(f"EXECUTION_SLICE_ALREADY_OWNED:{conflict['grant_id']}:{conflict['state']}")
        grant_id=str(uuid.uuid4())
        runner_id=execution_slice.get("runner_id") or select_task_runner(execution_slice)
        runtime_id=execution_slice.get("execution_runtime") or runtime_for_task(execution_slice)
        execution_slice={**execution_slice,"runner_id":runner_id,"execution_runtime":runtime_id}
        grant={"schema_version":5,
               "grant_id":grant_id,
               "worker_id":execution_slice.get("worker_id"),
               "contract_sha256":hashlib.sha256(contract_bytes).hexdigest(),
               "activation_sha256":hashlib.sha256(activation_bytes).hexdigest(),
               "products":{
            name:{"repositories":activation["products"][name]["repositories"]}
            for name in selected_products},
               "execution_slice":execution_slice}
        return reserve_execution_grant(
            state_root,grant,repo,exact_head,execution_slice,runner_id,runtime_id,
            planner_id,lock["worker"],slice_paths_conflict,
        )
    finally:
        release_grant_issue_lock(lock)

def mark_builder_running(builder_state:Path,builder_output:Path,*,invocation_id:str,
                         grant_path:Path,worker_id:str|None,runner_id:str,runtime_id:str)->dict:
    """Replace stale terminal worker telemetry before the new child process starts."""
    try:
        grant_id=json.loads(grant_path.read_text(encoding="utf-8")).get("grant_id")
    except (OSError,json.JSONDecodeError,TypeError):
        grant_id=None
    payload={"schema_version":1,"status":"BUILDER_RUNNING","invocation_id":invocation_id,
             "worker_id":worker_id,"runner_id":runner_id,"runtime_id":runtime_id,
             "started_at_utc":datetime.now(UTC).isoformat()}
    if grant_id:
        payload["grant_id"]=grant_id
    atomic(builder_state,payload)
    builder_output.write_text("",encoding="utf-8")
    return payload

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
    runtime_lease_id=grant_authority.get("runtime_lease_id")
    resource_lease_ids=grant_authority.get("resource_lease_ids",{})
    if not isinstance(resource_lease_ids,dict):
        raise RuntimeError("RESOURCE_LEASE_AUTHORITY_INVALID")
    if not runner_id or not runtime_id:
        raise RuntimeError("EXECUTION_GRANT_IDENTITY_REQUIRED")
    if not runtime_lease_id:
        raise RuntimeError("RUNTIME_LEASE_ID_REQUIRED")
    cmd=[sys.executable,str(repo_root/"scripts"/"autonomous_slice_builder.py"),
        "--workspace-root",str(workspace_root),"--state",str(builder_state),
        "--execution-grant",str(grant_path),"--invocation-id",invocation_id,
        "--runner-id",runner_id,"--runtime-id",runtime_id,
        "--runtime-lease-id",runtime_lease_id,
        "--runtime-lease-state",str(RUNTIME_LEASES.state_path),
        "--resource-lease-ids",json.dumps(resource_lease_ids,sort_keys=True)]
    mark_builder_running(builder_state,builder_output,invocation_id=invocation_id,
                         grant_path=grant_path,worker_id=worker_id,
                         runner_id=runner_id,runtime_id=runtime_id)
    p=subprocess.run(cmd,
        cwd=repo_root,text=True,capture_output=True,check=False)
    builder_output.write_text((p.stdout or "")+(p.stderr or ""),encoding="utf-8")
    finalize_unclaimed_grant(grant_path,p.returncode,builder_state)
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
    return _parallel_runtime.runner_requirements_for_task(task)


def select_task_runner(task:dict,registry:RunnerRegistry=DEFAULT_RUNNERS,
                       running:dict[str,int]|None=None)->str:
    return _parallel_runtime.select_task_runner(task,registry,running)


def runtime_for_task(task:dict)->str:
    return _parallel_runtime.runtime_for_task(task,require_runtime_capabilities)


def resource_leases_for_task(task:dict,runtime_id:str)->tuple[str,...]:
    return _parallel_runtime.resource_leases_for_task(task,runtime_id)


def runner_verified_history(state_root:Path)->dict[str,dict[str,int]]:
    return _parallel_runtime.runner_verified_history(state_root)


def task_affinity_class(task:dict)->str:
    return _planning.task_affinity_class(task)


def runner_affinity_history(state_root:Path,task_class:str)->dict[str,dict[str,int]]:
    return _parallel_runtime.runner_affinity_history(state_root,task_class)


def runner_productivity_history(state_root:Path,task_class:str|None=None)->dict[str,dict[str,float]]:
    return _planning.runner_productivity_history(state_root,task_class)


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

def reconcile_dashboard_ci(state_root:Path,workspace:Path)->dict:
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
        p=subprocess.run(cmd,cwd=workspace,text=True,capture_output=True,check=False)
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
    atomic(state_root/"dashboard-ci.json",payload)
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
    active=[{"grant_id":gid,"repository":r.get("repository") or (r.get("execution_slice") or {}).get("repository"),
             "slice_id":r.get("slice_id"),"runner_id":r.get("runner_id"),"state":r.get("state"),
             "planner_decision_id":r.get("planner_decision_id")}
            for gid,r in lifecycle.items() if isinstance(r,dict) and r.get("state") in {"ISSUED","CLAIMED","QUARANTINED"}]
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
        terminal.append({"grant_id":gid,"repository":r.get("repository") or (r.get("execution_slice") or {}).get("repository"),
                         "slice_id":r.get("slice_id"),"runner_id":r.get("runner_id"),"state":r.get("state"),
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
                     "measured_worker_seconds":elapsed_seconds},
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
    atomic(state_root/"telemetry"/"foundry-dashboard.json",payload)
    return payload

def productivity_penalty(runner_id:str,stats:dict[str,dict[str,float]])->float:
    return _parallel_runtime.productivity_penalty(runner_id,stats)


def adaptive_runner_score(manifest,history:dict[str,dict[str,int]],running:dict[str,int],
                          affinity:dict[str,dict[str,int]]|None=None,
                          productivity:dict[str,dict[str,float]]|None=None)->tuple[float,int,str]:
    return _parallel_runtime.adaptive_runner_score(manifest,history,running,affinity,productivity)


def runner_capacity_cooldowns(state_root:Path,now:datetime|None=None,cooldown_seconds:int=1800)->set[str]:
    return _parallel_runtime.runner_capacity_cooldowns(state_root,now,cooldown_seconds)


def select_adaptive_runner(task:dict,state_root:Path,registry:RunnerRegistry,
                           running:dict[str,int])->str:
    return _parallel_runtime.select_adaptive_runner(task,state_root,registry,running)


def durable_runner_occupancy(state_root:Path,now:datetime|None=None)->dict[str,int]:
    return _parallel_runtime.durable_runner_occupancy(
        state_root,now,liveness_probe=worker_liveness,
    )


def dispatch_parallel_builders(pool:ParallelWorkerPool,repo_root:Path,workspace_root:Path,
                               state_root:Path,products:list[str],
                               runner_registry:RunnerRegistry|None=None,
                               dispatch_invocation_id:str|None=None)->list[str]:
    return _parallel_runtime.dispatch_parallel_builders(
        pool,repo_root,workspace_root,state_root,products,
        ready_task_batch_fn=ready_task_batch,
        run_builder_for_product_fn=run_builder_for_product,
        runtime_leases=RUNTIME_LEASES,
        runtime_lease_recovery=RUNTIME_LEASE_RECOVERY,
        runner_registry=runner_registry,
        dispatch_invocation_id=dispatch_invocation_id,
    )



from project_orchestrator import workspace_recovery as _workspace_recovery

classify_orphan_branch = _workspace_recovery.classify_orphan_branch
resume_orphan_branch = _workspace_recovery.resume_orphan_branch
builder_artifacts_only = _workspace_recovery.builder_artifacts_only
preserved_work_evidence = _workspace_recovery.preserved_work_evidence
revalidate_preserved_work = _workspace_recovery.revalidate_preserved_work
issue_preserved_recovery_capability = _workspace_recovery.issue_preserved_recovery_capability
reconcile_consumed_recovery = _workspace_recovery.reconcile_consumed_recovery
reconcile_consumed_staged_work = _workspace_recovery.reconcile_consumed_staged_work
issue_staged_commit_capability = _workspace_recovery.issue_staged_commit_capability
pending_recovery_grants = _workspace_recovery.pending_recovery_grants
verify_durable_pr = _workspace_recovery.verify_durable_pr

def begin_recovery_generation(path:Path,grant_id:str,expected:dict)->tuple[str,dict]|None:
    return _workspace_recovery.begin_recovery_generation(
        path,grant_id,expected,
        liveness_probe=worker_liveness,
        fingerprint_probe=process_start_fingerprint,
    )

commit_recovery_record = _workspace_recovery.commit_recovery_record
reconcile_abandoned_claims = _workspace_recovery.reconcile_abandoned_claims

def validate_preserved_work(ws:Path,record:dict)->str:
    return _workspace_recovery.validate_preserved_work(ws,record,revalidate_preserved_work)

def consume_preserved_recovery_capability(ws:Path,record:dict)->str:
    return _workspace_recovery.consume_preserved_recovery_capability(ws,record,revalidate_preserved_work)

def commit_preserved_work(ws:Path,record:dict)->str:
    return _workspace_recovery.commit_preserved_work(ws,record,consume_preserved_recovery_capability)

def commit_reconciled_staged_work(ws:Path,record:dict)->str:
    return _workspace_recovery.commit_reconciled_staged_work(ws,record,reconcile_consumed_staged_work)

def recheck_preserved_work_quarantines(workspace_root:Path,state_root:Path)->dict[str,str]:
    return _workspace_recovery.recheck_preserved_work_quarantines(
        workspace_root,state_root,
        revalidate_fn=revalidate_preserved_work,
        validate_fn=validate_preserved_work,
        commit_fn=commit_preserved_work,
        reconcile_consumed_fn=reconcile_consumed_recovery,
        reconcile_staged_fn=reconcile_consumed_staged_work,
        issue_staged_fn=issue_staged_commit_capability,
        staged_commit_fn=commit_reconciled_staged_work,
    )

def productive_liveness(state_root:Path,authorized_products:list[str],now:datetime|None=None,stale_seconds:int=900,
                        builder_status:str|None=None)->dict:
    return _planning.productive_liveness(state_root,authorized_products,now,stale_seconds,builder_status)


def product_convergence(state_root:Path,authorized_products:list[str],*,current_invocation_id:str|None=None)->dict:
    return _planning.product_convergence(
        state_root,authorized_products,current_invocation_id=current_invocation_id,
    )


def request_liveness_reacquire(state_root:Path,liveness:dict)->dict:
    return _planning.request_liveness_reacquire(state_root,liveness,request_immediate_builder)


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

PRODUCT_BY_REPOSITORY=_planning.PRODUCT_BY_REPOSITORY


def product_for_repository(repository:str|None)->str|None:
    return _planning.product_for_repository(repository)


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
        dispatch_invocation_id=uuid.uuid4().hex if products else None
        dispatched_workers=dispatch_parallel_builders(
            worker_pool,repo,workspace,state,products,
            dispatch_invocation_id=dispatch_invocation_id
        ) if products else []
        # A completed nonproductive worker has already released its slot. Refill
        # immediately in the same burst pass instead of waiting for the next loop.
        # This turns ownership/no-work/exception outcomes into routing feedback.
        if products and parallel_results and any(not row.get("healthy",False) for row in parallel_results.values()):
            dispatch_invocation_id=uuid.uuid4().hex
            dispatched_workers.extend(
                dispatch_parallel_builders(worker_pool,repo,workspace,state,products,
                                           dispatch_invocation_id=dispatch_invocation_id)
            )
        pool_snapshot=worker_pool.snapshot()
        # Persist the authoritative post-harvest/post-dispatch pool, not the
        # earlier dispatch-time snapshot. Otherwise completed futures can remain
        # on disk as phantom RUNNING workers until another dispatch occurs.
        atomic(state/"parallel-worker-pool.json",{
            "schema_version":2,"pool":pool_snapshot,"runtime_leases":RUNTIME_LEASES.snapshot(),
            "dispatched":dispatched_workers,"dispatch_invocation_id":dispatch_invocation_id,
            "runtime_lease_recovery":RUNTIME_LEASE_RECOVERY,
            "recorded_at_utc":datetime.now(UTC).isoformat(),
        })
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
        convergence=product_convergence(state,products,current_invocation_id=dispatch_invocation_id)
        idle_streaks=_planning.record_eligible_idle_streaks(
            state,products,current_invocation_id=dispatch_invocation_id,
        )
        liveness_reacquire=request_liveness_reacquire(state,liveness)
        if convergence["status"]=="UNEXPLAINED_ELIGIBLE_IDLE":
            request_immediate_builder(state)
            liveness_reacquire={"requested":True,"reason":"PRODUCT_CONVERGENCE_DEFECT",
                "products":convergence["unexplained_eligible_products"],
                "requested_at_utc":datetime.now(UTC).isoformat()}
        healthy=(builder_ok and linear_rc in (None,0) and not quarantines
                 and liveness["status"] not in {"STALE_ACTIVE_WORK","ELIGIBLE_IDLE","AUTHORIZED_NO_SLICE"}
                 and convergence["status"]=="CONVERGED"
                 and idle_streaks["status"]!="P1_LIVENESS_DEFECT")
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
            "eligible_idle_streaks":idle_streaks,
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
        # Dashboard telemetry is observational only and must never consume or block
        # an execution opportunity. CI reconciliation failure degrades the UI but
        # does not change lifecycle or promotion authority.
        try:
            reconcile_dashboard_ci(state,repo)
        except (OSError,TypeError,subprocess.SubprocessError):
            pass
        try:
            write_dashboard_snapshot(state)
        except (OSError,TypeError,ValueError):
            pass
        record["burst_pass"]=burst_pass
        atomic(state/"continuation-current-burst.json",{"schema_version":2,"burst_id":burst_id,
            "process_instance_id":process_instance_id,
            "previous_process_instance_id":burst.get("previous_process_instance_id"),
            "resumed":burst["resumed"],"pass_count":burst_pass,"last_sequence":record["sequence"],"last_builder_status":builder_status,
            "immediate_continuation":record["immediate_continuation"],"updated_at_utc":datetime.now(UTC).isoformat()})
        time.sleep(delay)
if __name__=="__main__": raise SystemExit(main())

