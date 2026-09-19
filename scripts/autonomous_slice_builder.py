"""Idle-gated autonomous bounded-slice builder using local Ollama + Aider."""
from __future__ import annotations
import argparse, ctypes, hashlib, json, os, shutil, subprocess, sys, threading, time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
try:
    from scripts.lane_leases import open_pr_paths
    from scripts.autonomous_contract_guard import contract_guard_failure
except ModuleNotFoundError:
    from lane_leases import open_pr_paths
    from autonomous_contract_guard import contract_guard_failure

PRODUCT_REPOS={
    "knowledge-engine": ("jweter/knowledge-engine-core","jweter/knowledge-engine-web","jweter/knowledge-engine-ai"),
    "rocksmith": ("jweter/rocksmith-cdlc-generator",),
    "everward": ("jweter/Project-Everward",),
}
PORTFOLIO_REPOS=(
    "jweter/knowledge-engine-core",
    "jweter/knowledge-engine-web","jweter/knowledge-engine-ai",
    "jweter/rocksmith-cdlc-generator","jweter/Project-Everward",
)
MODEL=os.environ.get("EXPONENTIAL_BUILDER_MODEL","ollama/qwen2.5-coder:7b")
PROTECTED_PARTS=("dowork.txt","agents.md","license","security",".github/workflows",
                 "engineering/control-plane.json","roadmap","architecture","vision","policy")
PLACEHOLDER_MARKERS=("entire file content", "todo: implement", "your code here", "placeholder")
GENERIC_ROOT_FILES={"utils.py","util.py","helpers.py","helper.py","path/to/filename.js"}
MIN_IDLE_SECONDS=300
MAX_AIDER_SECONDS=2700
MAX_PREFLIGHT_SECONDS=7200
MAX_AUTONOMOUS_DELETIONS=80
MAX_AUTONOMOUS_CHANGED_LINES=400

def run(argv:list[str],cwd:Path,**kwargs:Any)->subprocess.CompletedProcess[str]:
    return subprocess.run(argv,cwd=cwd,text=True,capture_output=True,check=False,**kwargs)

def gh_json(args:list[str])->Any:
    p=subprocess.run(["gh",*args],text=True,capture_output=True,check=False)
    if p.returncode: raise RuntimeError(p.stderr.strip() or "gh failed")
    return json.loads(p.stdout)

def failure_fingerprint(stage:str,detail:str)->str:
    """Stable identity for recurring autonomous failures; exclude volatile run metadata."""
    normalized=" ".join(detail.lower().split())[:2000]
    return hashlib.sha256(f"{stage}|{normalized}".encode()).hexdigest()[:20]

def retain_failure_lesson(state_path:Path,stage:str,detail:str,outcome:str)->dict[str,Any]:
    """Persist evidence so repeated failures become reusable engineering knowledge."""
    path=state_path.with_name("autonomous-failure-lessons.json")
    try:
        payload=json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"schema_version":1,"lessons":{}}
    except (OSError,json.JSONDecodeError,TypeError):
        payload={"schema_version":1,"lessons":{}}
    fp=failure_fingerprint(stage,detail)
    lessons=payload.setdefault("lessons",{})
    lesson=lessons.get(fp,{"stage":stage,"fingerprint":fp,"occurrences":0,"first_seen":datetime.now(UTC).isoformat()})
    lesson["occurrences"]=int(lesson.get("occurrences",0))+1
    lesson["last_seen"]=datetime.now(UTC).isoformat()
    lesson["last_outcome"]=outcome
    lesson["detail"]=detail[:4000]
    lesson["promotion_candidate"]=lesson["occurrences"]>=2
    lessons[fp]=lesson
    atomic_json(path,payload)
    return lesson

def windows_idle_seconds()->float|None:
    if os.name!="nt": return None
    class LASTINPUTINFO(ctypes.Structure):
        _fields_=[("cbSize",ctypes.c_uint),("dwTime",ctypes.c_uint)]
    info=LASTINPUTINFO(); info.cbSize=ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)): return None
    return max(0.0,(ctypes.windll.kernel32.GetTickCount64()-info.dwTime)/1000.0)
def open_pr_count(repo:str)->int:
    return len(gh_json(["pr","list","--repo",repo,"--state","open","--json","number","--limit","20"]))

def lane_capacity(repo:str)->int:
    # Conservative parallelism: one existing PR may coexist with one independent
    # autonomous lane. Two open PRs keep the repository saturated.
    return max(0,2-open_pr_count(repo))

def clean_generated_workspace_artifacts(ws:Path)->None:
    # Dedicated actor workspaces may retain tool/preflight output between runs.
    cleanup_aider_artifacts(ws)
    evidence=ws/"preflight-evidence.json"
    if evidence.exists():
        tracked=run(["git","ls-files","--error-unmatch","preflight-evidence.json"],ws)
        if tracked.returncode==0:
            run(["git","restore","--source=HEAD","--","preflight-evidence.json"],ws)
        else:
            evidence.unlink()

def ensure_workspace(repo:str,root:Path)->Path:
    name=repo.split("/",1)[1]
    ws=root/name
    if not ws.exists():
        root.mkdir(parents=True,exist_ok=True)
        proc=subprocess.run(["gh","repo","clone",repo,str(ws),"--","--filter=blob:none"],
                            text=True,capture_output=True,check=False)
        if proc.returncode: raise RuntimeError("clone failed")
    origin=run(["git","remote","get-url","origin"],ws)
    if origin.returncode or repo.lower() not in origin.stdout.lower():
        raise RuntimeError("origin mismatch")
    clean_generated_workspace_artifacts(ws)
    dirty=run(["git","status","--porcelain"],ws)
    if dirty.returncode or dirty.stdout.strip():
        raise RuntimeError("workspace dirty")
    fetch=run(["git","fetch","--prune","origin"],ws)
    if fetch.returncode: raise RuntimeError("fetch failed")
    return ws

HEARTBEAT_SECONDS=60

def heartbeat_grant(grant_path:Path,stop:threading.Event,interval:int=HEARTBEAT_SECONDS)->None:
    lifecycle=grant_path.with_name("hands-off-grant-lifecycle.json")
    try:
        grant_id=json.loads(grant_path.read_text(encoding="utf-8"))["grant_id"]
    except (OSError,json.JSONDecodeError,KeyError,TypeError):
        return
    while not stop.wait(interval):
        try:
            payload=json.loads(lifecycle.read_text(encoding="utf-8"))
            record=payload["grants"][grant_id]
        except (OSError,json.JSONDecodeError,KeyError,TypeError):
            return
        if record.get("state")!="CLAIMED":
            return
        record["last_heartbeat_at"]=datetime.now(UTC).isoformat()
        atomic_json(lifecycle,payload)

def granted_repositories(path:Path,workspace_root:Path,contract_path:Path|None=None,
                         activation_path:Path|None=None,lifecycle_path:Path|None=None)->dict[str,str]:
    """Return repositories only when grant contract and all certified HEADs are still current."""
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
        contract=contract_path or (Path(__file__).resolve().parents[1]/"engineering"/"hands-off-evolution.json")
        contract_bytes=contract.read_bytes()
        contract_digest=hashlib.sha256(contract_bytes).hexdigest()
        declared_products=json.loads(contract_bytes).get("products",{})
        activation=activation_path or path.with_name("hands-off-activation.json")
        activation_digest=hashlib.sha256(activation.read_bytes()).hexdigest()
    except (OSError,json.JSONDecodeError):
        return {}
    grant_id=payload.get("grant_id")
    lifecycle=lifecycle_path or path.with_name("hands-off-grant-lifecycle.json")
    try:
        lifecycle_state=json.loads(lifecycle.read_text(encoding="utf-8")) if lifecycle.exists() else {"schema_version":1,"grants":{}}
        grants=lifecycle_state.get("grants",{})
    except (OSError,json.JSONDecodeError,TypeError):
        return {}
    if (payload.get("schema_version") != 4 or not isinstance(grant_id,str) or not grant_id
            or grant_id in grants
            or payload.get("contract_sha256") != contract_digest
            or payload.get("activation_sha256") != activation_digest):
        return {}
    allowed={}
    for product,value in payload.get("products",{}).items():
        repositories=value.get("repositories",{}) if isinstance(value,dict) else {}
        if (not repositories or product not in declared_products
                or set(repositories) != set(declared_products[product])):
            continue
        current=True
        for repo,expected in repositories.items():
            ws=workspace_root/repo.split("/",1)[-1]
            head=run(["git","rev-parse","HEAD"],ws) if ws.is_dir() else None
            if head is None or head.returncode or head.stdout.strip()!=expected:
                current=False
                break
        if current:
            allowed.update(repositories)
    return allowed

def claim_execution_grant(path:Path,repo:str,expected_sha:str,lifecycle_path:Path|None=None)->bool:
    """Atomically consume an exact-head capability immediately before mutation."""
    lifecycle=lifecycle_path or path.with_name("hands-off-grant-lifecycle.json")
    try:
        grant=json.loads(path.read_text(encoding="utf-8"))
        grant_id=grant["grant_id"]
        state=json.loads(lifecycle.read_text(encoding="utf-8")) if lifecycle.exists() else {"schema_version":1,"grants":{}}
        grants=state["grants"]
    except (OSError,json.JSONDecodeError,KeyError,TypeError):
        return False
    if grant_id in grants:
        return False
    claimed=datetime.now(UTC).isoformat()
    grants[grant_id]={"state":"CLAIMED","claimed_at":claimed,"last_heartbeat_at":claimed,
                      "authorized_repositories":[repo],"expected_sha":expected_sha}
    atomic_json(lifecycle,{"schema_version":1,"grants":grants})
    return True

def select_repo(state_path:Path,eligible_products:set[str]|None=None,eligible_repos:set[str]|None=None)->str|None:
    prior=-1
    if state_path.exists():
        try: prior=int(json.loads(state_path.read_text(encoding="utf-8")).get("last_repo_index",-1))
        except Exception: prior=-1
    if eligible_repos is not None:
        repo_universe=tuple(sorted(eligible_repos))
    elif eligible_products is None:
        repo_universe=PORTFOLIO_REPOS
    else:
        allowed={repo for product in eligible_products for repo in PRODUCT_REPOS.get(product,())}
        repo_universe=tuple(repo for repo in PORTFOLIO_REPOS if repo in allowed)
    if not repo_universe:
        return None
    for offset in range(1,len(repo_universe)+1):
        idx=(prior+offset)%len(repo_universe)
        repo=repo_universe[idx]
        if lane_capacity(repo)>0:
            return repo
    return None

def prompt_for(repo:str)->str:
    owned=open_pr_paths(repo)
    avoid=sorted({p for lease in owned for p in lease.paths})
    avoid_text=", ".join(avoid[:80]) if avoid else "none"
    return f"""You are the autonomous laptop builder for {repo}.
Read the repository's AGENTS.md, README, roadmap/milestone, architecture, and engineering/control-plane files before editing.
Choose exactly ONE small, high-value, authorized P4/P5 slice that is independent of Product Reality and does not require a human decision.
Open PRs already own these paths: {avoid_text}
Choose an independent slice that does not edit, rename, delete, or semantically depend on those owned paths. If no independent slice exists, make no changes.
Do not edit governance, roadmap, architecture, vision, security, license, workflow, or control-plane authority files.
Do not change secrets, credentials, releases, publishing, destructive migrations, or licensing.
Prefer source + regression test changes. Do not claim Product Reality. Do not commit, push, or merge.
Make the smallest coherent implementation that can pass the repository's canonical preflight."""
def run_aider(ws:Path,message:str)->subprocess.CompletedProcess[str]:
    env=os.environ.copy()
    env.setdefault("OLLAMA_API_BASE","http://127.0.0.1:11434")
    argv=["aider","--model",MODEL,"--message",message,
          "--no-auto-commits","--no-gitignore","--no-pretty","--no-stream",
          "--analytics-disable","--no-check-update","--no-show-model-warnings",
          "--no-suggest-shell-commands","--yes-always"]
    return run(argv,ws,env=env,timeout=MAX_AIDER_SECONDS)

def cleanup_aider_artifacts(ws:Path)->None:
    for path in ws.glob(".aider*"):
        if path.is_dir(): shutil.rmtree(path,ignore_errors=True)
        else:
            try: path.unlink()
            except FileNotFoundError: pass

def reset_workspace(ws:Path,base:str)->None:
    run(["git","reset","--hard",base],ws)
    run(["git","clean","-fd"],ws)

def changed_paths(ws:Path,base:str)->list[str]:
    p=run(["git","diff","--name-only",base,"HEAD"],ws)
    unstaged=run(["git","diff","--name-only"],ws)
    untracked=run(["git","ls-files","--others","--exclude-standard"],ws)
    values=set(p.stdout.splitlines()+unstaged.stdout.splitlines()+untracked.stdout.splitlines())
    return sorted(x for x in values if x)

def protected_change(paths:list[str])->str|None:
    for path in paths:
        low=path.replace("\\","/").lower()
        if any(part in low for part in PROTECTED_PARTS):
            return path
    return None

def semantic_quality_failure(ws:Path,paths:list[str])->str|None:
    normalized={p.replace("\\","/").lower() for p in paths}
    for path in normalized:
        if path in GENERIC_ROOT_FILES:
            return f"generic_or_placeholder_path:{path}"
        candidate=ws/path
        if candidate.is_file() and candidate.stat().st_size <= 200_000:
            try: text=candidate.read_text(encoding="utf-8",errors="ignore").lower()
            except OSError: continue
            marker=next((m for m in PLACEHOLDER_MARKERS if m in text),None)
            if marker: return f"placeholder_content:{path}:{marker}"
    source_like=[p for p in normalized if p.endswith((".py",".js",".ts",".tsx",".cpp",".h",".hpp",".cs")) and not p.startswith(("test","tests/"))]
    test_like=[p for p in normalized if p.startswith(("test","tests/")) or "/test" in p]
    if source_like and not test_like:
        return "source_change_without_regression_test"
    return None

def diff_anomaly_failure(ws:Path,base:str)->str|None:
    proc=run(["git","diff","--numstat",base,"--"],ws)
    if proc.returncode:
        return "diff_stat_unavailable"
    added=deleted=0
    for line in proc.stdout.splitlines():
        parts=line.split("\t")
        if len(parts)<3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        added+=int(parts[0]); deleted+=int(parts[1])
    if deleted>MAX_AUTONOMOUS_DELETIONS:
        return f"excessive_deletions:{deleted}"
    if added+deleted>MAX_AUTONOMOUS_CHANGED_LINES:
        return f"excessive_changed_lines:{added+deleted}"
    if deleted>=40 and deleted>max(added*2,40):
        return f"destructive_diff_ratio:add={added}:delete={deleted}"
    return None

def generated_change_failure(ws:Path,base:str,paths:list[str])->str|None:
    blocked=protected_change(paths)
    if blocked:
        return f"protected_path:{blocked}"
    contract=contract_guard_failure(ws,base,paths)
    if contract:
        return contract
    quality=semantic_quality_failure(ws,paths)
    if quality:
        return quality
    return diff_anomaly_failure(ws,base)

def repair_prompt(repo:str,failure:str,paths:list[str])->str:
    return f"""Your autonomous change for {repo} was rejected before PR creation.
Failure: {failure}
Changed paths: {", ".join(paths)}
Perform exactly ONE bounded repair attempt. Preserve all existing policy, authority, safety, security, provenance, licensing, Product Reality, and human-reserved boundaries. Do not weaken or replace configuration contracts. Reduce destructive diff size if flagged. Add regression coverage when source changed. If the requested repair cannot be made safely, revert your own changes and make no changes. Do not commit, push, or merge."""

def run_preflight(ws:Path)->subprocess.CompletedProcess[str]:
    script=ws/"engineering"/"preflight.py"
    if not script.is_file():
        raise RuntimeError("canonical preflight unavailable")
    env=os.environ.copy()
    env["PYTHONPATH"]=str(ws/"src")+os.pathsep+env.get("PYTHONPATH","")
    cmake_bin=Path.home()/"AppData/Roaming/Python/Python312/site-packages/cmake/data/bin"
    if cmake_bin.is_dir():
        env["PATH"]=str(cmake_bin)+os.pathsep+env.get("PATH","")
    return run([sys.executable,str(script)],ws,env=env,timeout=MAX_PREFLIGHT_SECONDS)

def atomic_json(path:Path,payload:dict[str,Any])->None:
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    tmp.replace(path)
def build_once(workspace_root:Path,state_path:Path,dry_run:bool=False,eligible_products:set[str]|None=None,eligible_repos:set[str]|None=None,
               execution_grant:Path|None=None)->dict[str,Any]:
    idle=windows_idle_seconds()
    if idle is not None and idle<MIN_IDLE_SECONDS:
        return {"status":"USER_ACTIVE","idle_seconds":idle}
    repo=select_repo(state_path,eligible_products,eligible_repos)
    if repo is None:
        return {"status":"NO_ELIGIBLE_REPOSITORY"}
    idx=PORTFOLIO_REPOS.index(repo)
    if dry_run:
        result={"status":"DRY_RUN","repository":repo,"last_repo_index":idx}
        atomic_json(state_path,result); return result

    ws=ensure_workspace(repo,workspace_root)
    main="main"
    reset=run(["git","checkout","-B","actor-staging",f"origin/{main}"],ws)
    if reset.returncode: raise RuntimeError("base checkout failed")
    base=run(["git","rev-parse","HEAD"],ws).stdout.strip()
    if execution_grant is not None:
        if not isinstance(eligible_repos,dict):
            return {"status":"EXECUTION_GRANT_INVALID","repository":repo}
        expected=eligible_repos.get(repo)
        if not expected or base!=expected:
            return {"status":"EXECUTION_GRANT_HEAD_MISMATCH","repository":repo,"expected_sha":expected,"actual_sha":base}
        if not claim_execution_grant(execution_grant,repo,expected):
            return {"status":"EXECUTION_GRANT_CLAIM_FAILED","repository":repo}
    stamp=datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    branch=f"actor/autonomous-{repo.split('/')[-1].lower()}-{stamp}"
    if run(["git","checkout","-B",branch,base],ws).returncode:
        raise RuntimeError("branch creation failed")

    aider=run_aider(ws,prompt_for(repo))
    cleanup_aider_artifacts(ws)
    paths=changed_paths(ws,base)
    if aider.returncode!=0:
        reset_workspace(ws,base)
        lesson=retain_failure_lesson(state_path,"aider_execution",aider.stderr or aider.stdout or f"returncode={aider.returncode}","BUILDER_FAILED")
        result={"status":"BUILDER_FAILED","repository":repo,"returncode":aider.returncode,"failure_fingerprint":lesson["fingerprint"],"last_repo_index":idx}
        atomic_json(state_path,result); return result
    if not paths:
        result={"status":"NO_OP","repository":repo,"last_repo_index":idx}
        atomic_json(state_path,result); return result
    initial_failure=generated_change_failure(ws,base,paths)
    if initial_failure:
        repair=run_aider(ws,repair_prompt(repo,initial_failure,paths))
        cleanup_aider_artifacts(ws)
        paths=changed_paths(ws,base)
        if repair.returncode!=0:
            reset_workspace(ws,base)
            result={"status":"AUTONOMOUS_CHANGE_REJECTED","repository":repo,
                    "initial_failure":initial_failure,"final_failure":"repair_execution_failed",
                    "repair_attempted":True,"last_repo_index":idx}
            atomic_json(state_path,result); return result
        if not paths:
            result={"status":"SELF_REPAIRED_TO_NO_OP","repository":repo,
                    "initial_failure":initial_failure,"repair_attempted":True,"last_repo_index":idx}
            atomic_json(state_path,result); return result
        final_failure=generated_change_failure(ws,base,paths)
        if final_failure:
            reset_workspace(ws,base)
            result={"status":"AUTONOMOUS_CHANGE_REJECTED","repository":repo,
                    "initial_failure":initial_failure,"final_failure":final_failure,
                    "repair_attempted":True,"last_repo_index":idx}
            atomic_json(state_path,result); return result
    preflight=run_preflight(ws)
    if preflight.returncode!=0:
        reset_workspace(ws,base)
        lesson=retain_failure_lesson(state_path,"preflight",preflight.stderr or preflight.stdout or f"returncode={preflight.returncode}","PREFLIGHT_FAILED")
        result={"status":"PREFLIGHT_FAILED","repository":repo,"returncode":preflight.returncode,"failure_fingerprint":lesson["fingerprint"],"last_repo_index":idx}
        atomic_json(state_path,result); return result
    run(["git","add","-A"],ws)
    commit=run(["git","commit","-m",f"actor autonomous bounded slice {stamp}"],ws)
    if commit.returncode:
        reset_workspace(ws,base)
        result={"status":"COMMIT_FAILED","repository":repo,"last_repo_index":idx}
        atomic_json(state_path,result); return result
    head=run(["git","rev-parse","HEAD"],ws).stdout.strip()
    push=run(["git","push","-u","origin",branch],ws)
    if push.returncode:
        result={"status":"PUSH_FAILED","repository":repo,"head":head,"last_repo_index":idx}
        atomic_json(state_path,result); return result
    linear_id=os.environ.get("EXPONENTIAL_LINEAR_ID","").strip()
    linear_ref=f" Linear: {linear_id}." if linear_id else ""
    body=("Autonomous laptop-actor bounded slice. Local Ollama/Aider produced the change; "
          "canonical exact-head preflight passed. Merge authority remains external and fail-closed."
          +linear_ref)
    created=subprocess.run(["gh","pr","create","--repo",repo,"--base",main,"--head",branch,
                            "--title","AUTO: bounded autonomous engineering slice","--body",body],
                           cwd=ws,text=True,capture_output=True,check=False)
    status="PR_OPEN" if created.returncode==0 else "PR_CREATE_FAILED"
    result={"status":status,"repository":repo,"branch":branch,"base_sha":base,
            "head_sha":head,"changed_paths":paths,"pr_url":created.stdout.strip(),
            "last_repo_index":idx}
    atomic_json(state_path,result)
    return result

def finish_grant_lifecycle(grant_path:Path,result:dict[str,Any])->None:
    lifecycle=grant_path.with_name("hands-off-grant-lifecycle.json")
    try:
        grant=json.loads(grant_path.read_text(encoding="utf-8"))
        state=json.loads(lifecycle.read_text(encoding="utf-8"))
        grant_id=grant["grant_id"]
        record=state["grants"][grant_id]
    except (OSError,json.JSONDecodeError,KeyError,TypeError):
        return
    record["state"]="COMPLETED" if result.get("status") in {
        "PR_OPEN","NO_OP","SELF_REPAIRED_TO_NO_OP","DRY_RUN","NO_ELIGIBLE_REPOSITORY"
    } else "FAILED"
    record["finished_at"]=datetime.now(UTC).isoformat()
    record["result_status"]=result.get("status","UNKNOWN")
    if result.get("repository"):
        record["repository"]=result["repository"]
    if result.get("branch"):
        record["branch"]=result["branch"]
    if result.get("base_sha"):
        record["base_sha"]=result["base_sha"]
    if result.get("head_sha"):
        record["head_sha"]=result["head_sha"]
    atomic_json(lifecycle,state)

def main()->int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--workspace-root",required=True)
    parser.add_argument("--state",required=True)
    parser.add_argument("--dry-run",action="store_true")
    parser.add_argument("--eligible-products",default=None,
                        help="Legacy manual/dev product filter")
    parser.add_argument("--execution-grant",type=Path,
                        help="Exact-head autonomous execution grant")
    args=parser.parse_args()
    state=Path(args.state)
    try:
        workspace=Path(args.workspace_root)
        if args.execution_grant:
            eligible_repos=granted_repositories(args.execution_grant,workspace)
            eligible=None
        else:
            eligible_repos=None
            eligible=None if args.eligible_products is None else {p for p in args.eligible_products.split(",") if p}
        stop_heartbeat=threading.Event()
        heartbeat=None
        if args.execution_grant and eligible_repos:
            heartbeat=threading.Thread(target=heartbeat_grant,args=(args.execution_grant,stop_heartbeat),daemon=True)
            heartbeat.start()
        try:
            result=build_once(workspace,state,args.dry_run,eligible,eligible_repos,args.execution_grant)
        finally:
            stop_heartbeat.set()
            if heartbeat:
                heartbeat.join(timeout=2)
        if args.execution_grant:
            finish_grant_lifecycle(args.execution_grant,result)
    except (OSError,RuntimeError,subprocess.TimeoutExpired,json.JSONDecodeError) as exc:
        result={"status":"P1_BUILDER_FAILURE","error_class":type(exc).__name__,"error":str(exc)[:500]}
        atomic_json(state,result)
    print(json.dumps(result,indent=2,sort_keys=True))
    return 0 if result["status"] not in {"P1_BUILDER_FAILURE","BUILDER_FAILED"} else 1

if __name__=="__main__":
    raise SystemExit(main())
