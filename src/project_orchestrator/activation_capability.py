from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from .grant_lifecycle import mutate_grant_lifecycle
from .state_io import atomic_json
from scripts.capability_slice_contract import derive_slice
from scripts.product_completion_proof import build_completion_proof

def observe_live_heads(repo_root:Path,workspace_root:Path,state_root:Path,run_fn=subprocess.run)->tuple[bool,str]:
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
        origin=run_fn(["git","remote","get-url","origin"],cwd=ws,text=True,capture_output=True,check=False)
        head=run_fn(["git","rev-parse","HEAD"],cwd=ws,text=True,capture_output=True,check=False)
        if origin.returncode or head.returncode or repo.lower() not in origin.stdout.lower():
            continue
        value=head.stdout.strip()
        if value:
            observed[repo]=value
    atomic_json(state_root/"hands-off-live-heads.json",observed)
    return bool(observed),"LIVE_HEADS_OBSERVED" if observed else "LIVE_HEADS_UNAVAILABLE"

def evolution_authorized(repo_root:Path,workspace_root:Path,state_root:Path,*,observe_fn=observe_live_heads,run_fn=subprocess.run)->tuple[bool,str,list[str],list[str]]:
    activation=state_root/"hands-off-activation.json"
    observed=state_root/"hands-off-live-heads.json"
    live_ok,_=observe_fn(repo_root,workspace_root,state_root)
    if not activation.exists() or not live_ok or not observed.exists():
        return False,"ACTIVATION_EVIDENCE_MISSING",[],[]
    p=run_fn([sys.executable,str(repo_root/"scripts"/"evolution_activation_gate.py"),
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

def refresh_readiness(repo_root:Path,workspace_root:Path,state_root:Path,run_fn=subprocess.run)->tuple[bool,str]:
    """Regenerate exact-head activation evidence from contract-declared repositories."""
    try:
        contract=json.loads((repo_root/"engineering"/"hands-off-evolution.json").read_text(encoding="utf-8"))
        products=contract.get("products",{})
        repo_ids=sorted({
            repo
            for declared in products.values()
            if isinstance(declared,list)
            for repo in declared
            if isinstance(repo,str) and "/" in repo
        })
    except (OSError,json.JSONDecodeError,TypeError,AttributeError):
        return False,"READINESS_CONTRACT_UNREADABLE"
    if not repo_ids:
        return False,"READINESS_CONTRACT_UNREADABLE"
    repos=[workspace_root/repo.split("/",1)[-1] for repo in repo_ids]
    if not all(path.is_dir() for path in repos):
        return False,"READINESS_WORKSPACE_INCOMPLETE"
    activation=state_root/"hands-off-activation.json"
    report=state_root/"hands-off-readiness.json"
    cmd=[sys.executable,str(repo_root/"scripts"/"hands_off_readiness.py"),
         *[str(path) for path in repos],"--output",str(report),"--activation-state",str(activation)]
    p=run_fn(cmd,cwd=repo_root,text=True,capture_output=True,check=False)
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
    atomic_json(state_root/"hands-off-expected-heads.json",heads)
    return True,"READINESS_REFRESHED"

def recertify_product(repo_root:Path,workspace_root:Path,state_root:Path,product:str,*,merge_fn=None,run_fn=subprocess.run)->tuple[bool,str]:
    """Audit one stale product and merge only its new authority."""
    contract_path=repo_root/"engineering"/"hands-off-evolution.json"
    merge_fn = merge_fn or merge_product_recertification
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
    p=run_fn(cmd,cwd=repo_root,text=True,capture_output=True,check=False)
    (state_root/f"recertify-{product}-last-output.txt").write_text((p.stdout or "")+(p.stderr or ""),encoding="utf-8")
    try:
        payload=json.loads(partial.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError):
        return False,"RECERTIFICATION_UNREADABLE"
    return merge_fn(state_root,product,payload)

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
    atomic_json(path,current)
    atomic_json(state_root/"hands-off-expected-heads.json",current["heads"])
    return candidate.get("enabled") is True,"PRODUCT_RECERTIFIED" if candidate.get("enabled") is True else "PRODUCT_QUARANTINED"

def request_immediate_builder(state_root:Path)->None:
    """Record that PR lifecycle progress should immediately feed the next build cycle."""
    atomic_json(state_root/"continuation-request.json",{
        "requested_at_utc":datetime.now(UTC).isoformat(),
        "reason":"PR_LIFECYCLE_ADVANCED",
    })

def reconcile_merge_feedback(state_root:Path,merged:list[dict],*,slice_fn=None,capability_fn=None)->list[dict]:
    """Advance exact merged slices into capability state before selecting more work."""
    slice_fn = slice_fn or reconcile_verified_merge
    capability_fn = capability_fn or reconcile_capability_from_slice_evidence
    outcomes=[]
    for event in merged:
        slice_result=slice_fn(state_root,event)
        outcome={"event":event,"slice":slice_result}
        capability_id=slice_result.get("capability_id")
        if capability_id:
            outcome["capability"]=capability_fn(
                state_root,event.get("repository"),capability_id)
        outcomes.append(outcome)
    return outcomes

def merge_watcher_advanced(state_root:Path,*,reconcile_fn=None)->bool:
    """Consume each successful merge observation exactly once, after product-state reconciliation."""
    reconcile_fn = reconcile_fn or reconcile_merge_feedback
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
    outcomes=reconcile_fn(state_root,event["merged"])
    blocking={"MERGE_IDENTITY_INCOMPLETE","LIFECYCLE_UNAVAILABLE","MERGE_SLICE_IDENTITY_UNPROVEN",
              "COMPLETION_EVIDENCE_CONFLICT","CAPABILITY_GRAPH_UNAVAILABLE","CAPABILITY_IDENTITY_UNPROVEN",
              "CAPABILITY_SLICES_REQUIRED","CAPABILITY_SLICE_IDENTITY_UNPROVEN","SLICE_EVIDENCE_UNREADABLE",
              "SLICE_EVIDENCE_IDENTITY_CONFLICT","CAPABILITY_COMPLETION_CONFLICT"}
    if any(item.get("slice",{}).get("status") in blocking
           or item.get("capability",{}).get("status") in blocking for item in outcomes):
        atomic_json(state_root/"merge-feedback-blocked.json",{"event":event,"outcomes":outcomes})
        return False
    blocked=state_root/"merge-feedback-blocked.json"
    if blocked.exists(): blocked.unlink()
    atomic_json(state_root/"merge-feedback-last.json",{"event":event,"outcomes":outcomes})
    atomic_json(cursor,event)
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
    atomic_json(path,evidence)
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
        atomic_json(graph_path,graph)
    return {"status":"CAPABILITY_VERIFIED_COMPLETE" if complete else "CAPABILITY_IN_PROGRESS",
            "capability_id":capability_id,"changed":changed}

def reconcile_product_completion(state_root:Path,repo:str,exact_head:str,payload:dict,*,derive_fn=derive_slice,proof_fn=build_completion_proof)->dict:
    """Promote exact-head final evidence into authoritative product completion."""
    graph=derive_fn(payload,repository=repo,exact_head=exact_head)
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
    proof=proof_fn(exact_head=exact_head,acceptance=acceptance,product_reality=reality)
    if proof.get("status")!="VERIFIED":
        return proof
    payload["product_completion"]=proof["product_completion"]
    graph_path=state_root/"capability-graphs"/f"{repo.replace('/','__')}.json"
    atomic_json(graph_path,payload)
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
    atomic_json(direction_dir/f"{repo.replace('/','__')}.json",request)
    exhausted_dir=state_root/"slice-exhaustion-evidence"
    exhausted_dir.mkdir(parents=True,exist_ok=True)
    atomic_json(exhausted_dir/f"{repo.replace('/','__')}__{execution_slice.get('slice_id','unknown')}.json",{
        "schema_version":1,
        "repository":repo,
        "exact_head":exact_head,
        "capability_id":execution_slice.get("capability_id"),
        "slice_id":execution_slice.get("slice_id"),
        **evidence,
        "recorded_at_utc":datetime.now(UTC).isoformat(),
    })

def load_execution_slice(state_root:Path,repo:str,exact_head:str,*,derive_fn=derive_slice,reconcile_completion_fn=None)->dict:
    """Derive bounded mutation authority from the repository's machine-readable capability graph."""
    reconcile_completion_fn = reconcile_completion_fn or reconcile_product_completion
    path=state_root/"capability-graphs"/f"{repo.replace('/','__')}.json"
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as exc:
        direction_dir=state_root/"direction-requests"
        direction_dir.mkdir(parents=True,exist_ok=True)
        atomic_json(direction_dir/f"{repo.replace('/','__')}.json",{
            "schema_version":1,"status":"DIRECTION_REQUIRED","repository":repo,"exact_head":exact_head,
            "reason":"CAPABILITY_GRAPH_REQUIRED",
            "issues":[{"code":"CAPABILITY_GRAPH_REQUIRED","detail":type(exc).__name__}],
            "requested_at_utc":datetime.now(UTC).isoformat(),
        })
        raise RuntimeError(f"CAPABILITY_GRAPH_REQUIRED:{repo}")
    derived=derive_fn(payload,repository=repo,exact_head=exact_head)
    if derived.get("status")=="DIRECTION_REQUIRED" and derived.get("issues")==[{"code":"PRODUCT_COMPLETION_EVIDENCE_REQUIRED"}]:
        reconciliation=reconcile_completion_fn(state_root,repo,exact_head,payload)
        if reconciliation.get("status")=="PRODUCT_COMPLETION_VERIFIED":
            derived=derive_fn(payload,repository=repo,exact_head=exact_head)
    request_path=state_root/"direction-requests"/f"{repo.replace('/','__')}.json"
    if derived.get("status")=="NO_WORK":
        request_path.unlink(missing_ok=True)
        reason = derived.get("reason") or "NO_WORK"
        if reason == "ROADMAP_EXHAUSTED":
            atomic_json(
                state_root / "continuation-request.json",
                {
                    "requested_at_utc": datetime.now(UTC).isoformat(),
                    "reason": "REFRESH_AUTHORIZED_CANDIDATES",
                    "repository": repo,
                    "exact_head": exact_head,
                    "trigger": "ROADMAP_EXHAUSTED",
                },
            )
        return {
            "status":"NO_WORK",
            "reason":reason,
            "repository":repo,
            "exact_head":exact_head,
        }
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
        atomic_json(direction_dir/f"{repo.replace('/','__')}.json",request)
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
    atomic_json(state_root/"direction-outbox.json",{
        "schema_version":1,
        "updated_at_utc":datetime.now(UTC).isoformat(),
        "messages":direction_request_outbox(state_root),
    })

