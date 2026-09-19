"""Current-user continuous engineering supervisor; no admin Task Scheduler required."""
from __future__ import annotations
import argparse, json, subprocess, sys, time
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

def evolution_authorized(repo_root:Path,state_root:Path)->tuple[bool,str,list[str]]:
    activation=state_root/"hands-off-activation.json"
    expected=state_root/"hands-off-expected-heads.json"
    if not activation.exists() or not expected.exists():
        return False,"ACTIVATION_EVIDENCE_MISSING",[]
    p=subprocess.run([sys.executable,str(repo_root/"scripts"/"evolution_activation_gate.py"),
        "--activation-state",str(activation),"--expected-heads",str(expected)],
        cwd=repo_root,text=True,capture_output=True,check=False)
    (state_root/"activation-last-output.txt").write_text((p.stdout or "")+(p.stderr or ""),encoding="utf-8")
    try:
        payload=json.loads(p.stdout)
        reason=payload.get("reason","ACTIVATION_UNKNOWN")
        products=sorted(name for name,result in payload.get("products",{}).items()
                        if isinstance(result,dict) and result.get("allowed") is True)
    except (json.JSONDecodeError,TypeError):
        reason="ACTIVATION_UNREADABLE"
        products=[]
    return p.returncode==0 and bool(products),reason,products

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

def run_builder(repo_root:Path,workspace_root:Path,state_root:Path,products:list[str]|None=None)->int:
    cmd=[sys.executable,str(repo_root/"scripts"/"autonomous_slice_builder.py"),
        "--workspace-root",str(workspace_root),"--state",str(state_root/"builder.json")]
    if products is not None:
        cmd.extend(["--eligible-products",",".join(products)])
    p=subprocess.run(cmd,
        cwd=repo_root,text=True,capture_output=True,check=False)
    (state_root/"builder-last-output.txt").write_text((p.stdout or "")+(p.stderr or ""),encoding="utf-8")
    return p.returncode

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
        authorized,activation_reason,products=evolution_authorized(repo,state)
        if not authorized and activation_reason in {"ACTIVATION_EVIDENCE_MISSING","ACTIVATION_HEADS_STALE"}:
            refreshed,refresh_reason=refresh_readiness(repo,workspace,state)
            if refreshed:
                authorized,activation_reason,products=evolution_authorized(repo,state)
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
            "pr_lifecycle_advanced":lifecycle_advanced,
            "merge_watcher_pid":merge.pid,"merge_watcher_running":merge.poll() is None})
        if a.once:
            merge.terminate(); return 0 if rc==0 else 1
        # A merge exposes new roadmap work. Do not impose the normal idle delay
        # after lifecycle advancement; re-enter selection immediately.
        time.sleep(1 if lifecycle_advanced else max(60,a.builder_seconds))
if __name__=="__main__": raise SystemExit(main())
