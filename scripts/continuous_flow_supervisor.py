"""Current-user continuous engineering supervisor; no admin Task Scheduler required."""
from __future__ import annotations
import argparse, hashlib, json, subprocess, sys, time, uuid
from datetime import UTC, datetime
from pathlib import Path

def atomic(path:Path,payload:dict)->None:
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    tmp.replace(path)

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

def builder_health(state_root:Path,returncode:int)->tuple[bool,str]:
    path=state_root/"builder.json"
    if returncode != 0:
        return False,"PROCESS_FAILURE"
    try:
        status=json.loads(path.read_text(encoding="utf-8")).get("status","UNKNOWN")
    except (OSError,json.JSONDecodeError):
        return False,"STATE_UNREADABLE"
    degraded={"P1_BUILDER_FAILURE","BUILDER_FAILED","PREFLIGHT_FAILED","PUSH_FAILED","PR_CREATE_FAILED"}
    return status not in degraded,status

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
    contract_path=repo_root/"engineering"/"hands-off-evolution.json"
    try:
        contract=json.loads(contract_path.read_text(encoding="utf-8"))
        declared=contract["products"][product]
    except (OSError,json.JSONDecodeError,KeyError,TypeError):
        return False,"RECERTIFICATION_CONTRACT_UNREADABLE"
    repos=[workspace_root/repo.split("/",1)[-1] for repo in declared]
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

def merge_watcher_advanced(state_root:Path)->bool:
    """Consume each successful merge observation exactly once."""
    path=state_root/"green-pr-watcher.json"
    cursor=state_root/"green-pr-watcher-consumed.json"
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError):
        return False
    merged=[item for item in payload.get("results",[]) if item.get("merged")]
    if not merged:
        return False
    event={"updated_at_utc":payload.get("updated_at_utc"),"merged":[
        {"repository":item.get("repository"),"pr":item.get("pr"),"head":item.get("head")} for item in merged]}
    try:
        previous=json.loads(cursor.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError):
        previous=None
    if previous==event:
        return False
    atomic(cursor,event)
    return True

def write_execution_grant(repo_root:Path,state_root:Path,products:list[str])->Path:
    activation_path=state_root/"hands-off-activation.json"
    activation_bytes=activation_path.read_bytes()
    activation=json.loads(activation_bytes)
    contract_path=repo_root/"engineering"/"hands-off-evolution.json"
    contract_bytes=contract_path.read_bytes()
    grant={"schema_version":4,
           "grant_id":str(uuid.uuid4()),
           "contract_sha256":hashlib.sha256(contract_bytes).hexdigest(),
           "activation_sha256":hashlib.sha256(activation_bytes).hexdigest(),
           "products":{
        name:{"repositories":activation["products"][name]["repositories"]}
        for name in products}}
    path=state_root/"hands-off-execution-grant.json"
    atomic(path,grant)
    return path

def run_builder(repo_root:Path,workspace_root:Path,state_root:Path,products:list[str]|None=None)->int:
    cmd=[sys.executable,str(repo_root/"scripts"/"autonomous_slice_builder.py"),
        "--workspace-root",str(workspace_root),"--state",str(state_root/"builder.json")]
    if products is not None:
        cmd.extend(["--execution-grant",str(write_execution_grant(repo_root,state_root,products))])
    p=subprocess.run(cmd,
        cwd=repo_root,text=True,capture_output=True,check=False)
    (state_root/"builder-last-output.txt").write_text((p.stdout or "")+(p.stderr or ""),encoding="utf-8")
    return p.returncode

CLAIM_LEASE_SECONDS=1800

def recover_stale_grant_claims(state_root:Path,now:datetime|None=None)->list[str]:
    """Mark crash-stranded capability claims abandoned after their lease expires."""
    path=state_root/"hands-off-grant-lifecycle.json"
    if not path.exists():
        return []
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
        grants=payload.get("grants",{})
    except (OSError,json.JSONDecodeError,TypeError):
        return []
    current=now or datetime.now(UTC)
    abandoned=[]
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
        if age > CLAIM_LEASE_SECONDS:
            record["state"]="ABANDONED"
            record["finished_at"]=current.isoformat()
            record["result_status"]="CLAIM_LEASE_EXPIRED"
            abandoned.append(grant_id)
    if abandoned:
        atomic(path,payload)
    return abandoned

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

def reconcile_abandoned_claims(workspace_root:Path,state_root:Path,grant_ids:list[str])->dict[str,str]:
    """Inspect abandoned workspaces without deleting evidence; only clean main is auto-recoverable."""
    path=state_root/"hands-off-grant-lifecycle.json"
    if not grant_ids or not path.exists():
        return {}
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
        grants=payload["grants"]
    except (OSError,json.JSONDecodeError,KeyError,TypeError):
        return {grant_id:"LIFECYCLE_UNREADABLE" for grant_id in grant_ids}
    outcomes={}
    for grant_id in grant_ids:
        record=grants.get(grant_id,{})
        repos=record.get("authorized_repositories",[])
        repo=record.get("repository")
        candidates=[repo] if repo else repos
        if len(candidates)!=1:
            outcomes[grant_id]="RECOVERY_IDENTITY_AMBIGUOUS"; continue
        repo=candidates[0]; ws=workspace_root/repo.split("/",1)[-1]
        if not ws.is_dir():
            outcomes[grant_id]="WORKSPACE_MISSING"; continue
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
    atomic(path,payload)
    return outcomes

def main()->int:
    p=argparse.ArgumentParser()
    p.add_argument("--repo-root",required=True); p.add_argument("--workspace-root",required=True)
    p.add_argument("--state-root",required=True); p.add_argument("--builder-seconds",type=int,default=300)
    p.add_argument("--merge-seconds",type=int,default=60); p.add_argument("--once",action="store_true")
    a=p.parse_args(); repo=Path(a.repo_root); workspace=Path(a.workspace_root); state=Path(a.state_root)
    state.mkdir(parents=True,exist_ok=True)
    merge=None
    while True:
        if merge is None or merge.poll() is not None:
            merge=start_merge_watcher(repo,state,a.merge_seconds)
        linear_rc=run_linear_reconciliation(repo,state)
        abandoned_grants=recover_stale_grant_claims(state)
        recovery_results=reconcile_abandoned_claims(workspace,state,abandoned_grants)
        authorized,activation_reason,products,stale_products=evolution_authorized(repo,workspace,state)
        if stale_products:
            for product in stale_products:
                recertify_product(repo,workspace,state,product)
            authorized,activation_reason,products,stale_products=evolution_authorized(repo,workspace,state)
        if not authorized and activation_reason=="ACTIVATION_EVIDENCE_MISSING":
            refreshed,refresh_reason=refresh_readiness(repo,workspace,state)
            if refreshed:
                authorized,activation_reason,products,stale_products=evolution_authorized(repo,workspace,state)
            else:
                activation_reason=refresh_reason
        products=products if authorized else []
        rc=run_builder(repo,workspace,state,products) if products else 3
        builder_ok,builder_status=builder_health(state,rc)
        lifecycle_advanced=merge_watcher_advanced(state)
        if lifecycle_advanced:
            request_immediate_builder(state)
        healthy=builder_ok and linear_rc in (None,0)
        atomic(state/"supervisor.json",{"updated_at_utc":datetime.now(UTC).isoformat(),
            "status":"HEALTHY" if healthy else "FLOW_DEGRADED","builder_returncode":rc,
            "builder_status":builder_status,
            "linear_reconciliation_returncode":linear_rc,
            "evolution_authorized":authorized,"activation_reason":activation_reason,
            "authorized_products":products,
            "abandoned_grants":abandoned_grants,
            "abandoned_recovery":recovery_results,
            "pr_lifecycle_advanced":lifecycle_advanced,
            "merge_watcher_pid":merge.pid,"merge_watcher_running":merge.poll() is None})
        if a.once:
            merge.terminate(); return 0 if rc==0 else 1
        # A merge exposes new roadmap work. Do not impose the normal idle delay
        # after lifecycle advancement; re-enter selection immediately.
        time.sleep(1 if lifecycle_advanced else max(60,a.builder_seconds))
if __name__=="__main__": raise SystemExit(main())
