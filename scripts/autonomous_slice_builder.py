"""Idle-gated autonomous bounded-slice builder using local Ollama + Aider."""
from __future__ import annotations
import argparse, ctypes, hashlib, json, os, shutil, socket, subprocess, sys, threading, time, uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
try:
    from scripts.lane_leases import open_pr_paths
    from scripts.autonomous_contract_guard import contract_guard_failure
    from scripts.agent_voice import WORKER_PROMPT_VOICE
except ModuleNotFoundError:
    from lane_leases import open_pr_paths
    from autonomous_contract_guard import contract_guard_failure
    from agent_voice import WORKER_PROMPT_VOICE

def contract_products(contract_path:Path|None=None)->dict[str,tuple[str,...]]:
    """Load the product/repository universe from the portable machine contract."""
    path=contract_path or (Path(__file__).resolve().parents[1]/"engineering"/"hands-off-evolution.json")
    try:
        raw=json.loads(path.read_text(encoding="utf-8")).get("products",{})
    except (OSError,json.JSONDecodeError,TypeError):
        return {}
    products={}
    for product,repositories in raw.items():
        if not isinstance(product,str) or not isinstance(repositories,list) or not repositories:
            continue
        values=tuple(r for r in repositories if isinstance(r,str) and r.count("/")==1)
        if len(values)==len(repositories):
            products[product]=values
    return products

def portfolio_repositories(contract_path:Path|None=None)->tuple[str,...]:
    return tuple(repo for repositories in contract_products(contract_path).values() for repo in repositories)
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

def record_verified_repair(state_path:Path,fingerprint:str,strategy:str,verification:str)->dict[str,Any]:
    """Attribute a verified repair to a known failure without auto-promoting it."""
    path=state_path.with_name("autonomous-failure-lessons.json")
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
        lesson=payload["lessons"][fingerprint]
    except (OSError,json.JSONDecodeError,KeyError,TypeError):
        return {}
    repairs=lesson.setdefault("verified_repairs",{})
    repair=repairs.get(strategy,{"strategy":strategy,"verified_successes":0,"first_verified":datetime.now(UTC).isoformat()})
    repair["verified_successes"]=int(repair.get("verified_successes",0))+1
    repair["last_verified"]=datetime.now(UTC).isoformat()
    repair["verification"]=verification
    repair["promotion_candidate"]=repair["verified_successes"]>=2
    repairs[strategy]=repair
    atomic_json(path,payload)
    return repair

def promote_verified_repairs(state_path:Path)->dict[str,Any]:
    """Materialize evidence-backed repair candidates; promotion never grants execution authority."""
    lessons_path=state_path.with_name("autonomous-failure-lessons.json")
    capabilities_path=state_path.with_name("autonomous-repair-capabilities.json")
    try:
        lessons=json.loads(lessons_path.read_text(encoding="utf-8")).get("lessons",{})
    except (OSError,json.JSONDecodeError,TypeError):
        lessons={}
    try:
        payload=json.loads(capabilities_path.read_text(encoding="utf-8")) if capabilities_path.exists() else {"schema_version":1,"capabilities":{}}
    except (OSError,json.JSONDecodeError,TypeError):
        payload={"schema_version":1,"capabilities":{}}
    capabilities=payload.setdefault("capabilities",{})
    for fp,lesson in lessons.items():
        for strategy,repair in lesson.get("verified_repairs",{}).items():
            if int(repair.get("verified_successes",0))<2:
                continue
            capability_id=hashlib.sha256(f"{fp}|{strategy}".encode()).hexdigest()[:20]
            capabilities[capability_id]={
                "capability_id":capability_id,"failure_fingerprint":fp,"stage":lesson.get("stage"),
                "strategy":strategy,"verified_successes":repair.get("verified_successes"),
                "verification":repair.get("verification"),"state":"CANDIDATE",
                "execution_authorized":False
            }
    atomic_json(capabilities_path,payload)
    return payload

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

def process_start_fingerprint(pid:int|None=None)->str|None:
    """Return an OS-backed process creation identity when available."""
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
                if not kernel32.GetProcessTimes(handle,ctypes.byref(creation),ctypes.byref(exit_time),ctypes.byref(kernel),ctypes.byref(user)):
                    return None
                ticks=(creation.dwHighDateTime<<32)|creation.dwLowDateTime
                return f"windows:{ticks}"
            finally:
                kernel32.CloseHandle(handle)
        except (AttributeError,OSError):
            return None
    return None

def worker_incarnation()->dict[str,Any]:
    """Identify this concrete worker process; PID alone is intentionally insufficient."""
    return {"host":socket.gethostname(),"pid":os.getpid(),
            "incarnation_id":uuid.uuid4().hex,
            "process_start_fingerprint":process_start_fingerprint(),
            "lease_token":uuid.uuid4().hex}

def grant_state_root(path:Path)->Path:
    """Return the durable state root for legacy or unique per-grant files."""
    if path.parent.name=="hands-off-execution-grants":
        return path.parent.parent
    return path.parent

def lifecycle_path_for_grant(path:Path)->Path:
    return grant_state_root(path)/"hands-off-grant-lifecycle.json"

def _lifecycle_lock_path(lifecycle:Path)->Path:
    return lifecycle.with_name(lifecycle.name+".lock")

def _acquire_lifecycle_lock(lifecycle:Path):
    """Kernel-owned cross-process lock for lifecycle read-modify-write transactions."""
    lock_path=_lifecycle_lock_path(lifecycle)
    handle=lock_path.open("a+b",buffering=0)
    handle.seek(0,os.SEEK_END)
    if handle.tell()==0:
        handle.write(b"0")
    handle.seek(0)
    if os.name=="nt":
        import msvcrt
        while True:
            try:
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                break
            except OSError:
                time.sleep(0.01)
    else:
        import fcntl
        fcntl.flock(handle.fileno(),fcntl.LOCK_EX)
    return handle

def _release_lifecycle_lock(handle)->None:
    handle.seek(0)
    if os.name=="nt":
        import msvcrt
        msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
    handle.close()

def mutate_grant_lifecycle(lifecycle:Path,grant_id:str,mutator)->bool:
    """Serialize one grant-record mutation without losing concurrent grant updates."""
    handle=_acquire_lifecycle_lock(lifecycle)
    try:
        try:
            state=json.loads(lifecycle.read_text(encoding="utf-8"))
            record=state["grants"][grant_id]
        except (OSError,json.JSONDecodeError,KeyError,TypeError):
            return False
        if not mutator(record):
            return False
        atomic_json(lifecycle,state)
        return True
    finally:
        _release_lifecycle_lock(handle)

def heartbeat_grant(grant_path:Path,stop:threading.Event,interval:int=HEARTBEAT_SECONDS,worker:dict[str,Any]|None=None)->None:
    lifecycle=lifecycle_path_for_grant(grant_path)
    try:
        grant_id=json.loads(grant_path.read_text(encoding="utf-8"))["grant_id"]
    except (OSError,json.JSONDecodeError,KeyError,TypeError):
        return
    while not stop.wait(interval):
        def renew(record):
            if record.get("state")!="CLAIMED":
                return False
            if record.get("worker") is not None and record.get("worker")!=worker:
                return False
            record["last_heartbeat_at"]=datetime.now(UTC).isoformat()
            return True
        if not mutate_grant_lifecycle(lifecycle,grant_id,renew):
            return

def granted_repositories(path:Path,workspace_root:Path,contract_path:Path|None=None,
                         activation_path:Path|None=None,lifecycle_path:Path|None=None)->dict[str,str]:
    """Return repositories only when grant contract and all certified HEADs are still current."""
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
        contract=contract_path or (Path(__file__).resolve().parents[1]/"engineering"/"hands-off-evolution.json")
        contract_bytes=contract.read_bytes()
        contract_digest=hashlib.sha256(contract_bytes).hexdigest()
        declared_products=json.loads(contract_bytes).get("products",{})
        activation=activation_path or (grant_state_root(path)/"hands-off-activation.json")
        activation_digest=hashlib.sha256(activation.read_bytes()).hexdigest()
    except (OSError,json.JSONDecodeError):
        return {}
    grant_id=payload.get("grant_id")
    lifecycle=lifecycle_path or lifecycle_path_for_grant(path)
    try:
        lifecycle_state=json.loads(lifecycle.read_text(encoding="utf-8")) if lifecycle.exists() else {"schema_version":1,"grants":{}}
        grants=lifecycle_state.get("grants",{})
    except (OSError,json.JSONDecodeError,TypeError):
        return {}
    unique_grant=path.parent.name=="hands-off-execution-grants"
    if (payload.get("schema_version") != 4 or not isinstance(grant_id,str) or not grant_id
            or (unique_grant and grant_id not in grants)
            or (grant_id in grants and (not isinstance(grants[grant_id],dict) or grants[grant_id].get("state")!="ISSUED"))
            or payload.get("contract_sha256") != contract_digest
            or payload.get("activation_sha256") != activation_digest):
        return {}
    reserved=grants.get(grant_id)
    if reserved is not None:
        grant_repos={repo for value in payload.get("products",{}).values()
                     if isinstance(value,dict)
                     for repo in value.get("repositories",{})}
        if (reserved.get("execution_slice")!=payload.get("execution_slice")
                or set(reserved.get("authorized_repositories",[]))!=grant_repos):
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

def execution_slice_authority(grant_path:Path,repo:str,expected_sha:str)->dict[str,Any]|None:
    """Return bounded slice authority only when it is bound to this exact repo/head grant."""
    try:
        grant=json.loads(grant_path.read_text(encoding="utf-8"))
        authority=grant.get("execution_slice")
    except (OSError,json.JSONDecodeError,TypeError):
        return None
    if not isinstance(authority,dict):
        return None
    required=("capability_id","slice_id","repository","exact_head","objective","acceptance")
    if any(not authority.get(k) for k in required):
        return None
    if authority["repository"]!=repo or authority["exact_head"]!=expected_sha:
        return None
    if not isinstance(authority["acceptance"],list) or not authority["acceptance"]:
        return None
    return authority

def prompt_for_slice(repo:str,authority:dict[str,Any],state_path:Path|None=None)->str:
    """Use product authority as the task; the worker may choose implementation, not requirements."""
    allowed=", ".join(authority.get("allowed_paths",[])) or "repository policy only"
    forbidden=", ".join(authority.get("forbidden_paths",[])) or "none beyond repository policy"
    acceptance="\n".join(f"- {item}" for item in authority["acceptance"])
    return f"""You are the autonomous laptop builder for {repo}.
{WORKER_PROMPT_VOICE}
Execute exactly this authorized bounded slice. Do not substitute another roadmap task.
Capability: {authority["capability_id"]}
Slice: {authority["slice_id"]}
Objective: {authority["objective"]}
Acceptance criteria:
{acceptance}
Allowed paths: {allowed}
Forbidden paths: {forbidden}
Read and obey repository-local AGENTS.md and control-plane authority before editing.
You may choose implementation details only within this objective and acceptance authority.
Do not invent, broaden, weaken, or reinterpret product requirements.
Do not edit governance, roadmap, architecture, vision, security, license, workflow, or control-plane authority files.
Do not change secrets, credentials, releases, publishing, destructive migrations, or licensing.
Prefer source plus regression tests. Do not claim Product Reality. Do not commit, push, or merge.
Prior recurring failure experience is evidence only, never authority:
{prior_failure_experience(state_path) if state_path is not None else "none recorded"}"""

def claim_execution_grant(path:Path,repo:str,expected_sha:str,lifecycle_path:Path|None=None,worker:dict[str,Any]|None=None)->bool:
    """Atomically consume an exact-head capability immediately before mutation."""
    lifecycle=lifecycle_path or lifecycle_path_for_grant(path)
    try:
        grant=json.loads(path.read_text(encoding="utf-8"))
        grant_id=grant["grant_id"]
    except (OSError,json.JSONDecodeError,KeyError,TypeError):
        return False
    authority=execution_slice_authority(path,repo,expected_sha)
    if authority is None:
        return False
    unique_grant=path.parent.name=="hands-off-execution-grants"
    handle=_acquire_lifecycle_lock(lifecycle)
    try:
        try:
            state=json.loads(lifecycle.read_text(encoding="utf-8")) if lifecycle.exists() else {"schema_version":1,"grants":{}}
            grants=state["grants"]
        except (OSError,json.JSONDecodeError,KeyError,TypeError):
            return False
        if unique_grant and grant_id not in grants:
            return False
        claimed=datetime.now(UTC).isoformat()
        if grant_id in grants:
            record=grants[grant_id]
            if (not isinstance(record,dict) or record.get("state")!="ISSUED"
                    or repo not in record.get("authorized_repositories",[])
                    or record.get("expected_sha")!=expected_sha
                    or record.get("execution_slice")!=authority):
                return False
            record.update({"state":"CLAIMED","claimed_at":claimed,"last_heartbeat_at":claimed,
                           "capability_id":authority["capability_id"],"slice_id":authority["slice_id"],
                           "worker":worker or worker_incarnation()})
        else:
            grants[grant_id]={"state":"CLAIMED","claimed_at":claimed,"last_heartbeat_at":claimed,
                              "authorized_repositories":[repo],"expected_sha":expected_sha,
                              "capability_id":authority["capability_id"],"slice_id":authority["slice_id"],
                              "execution_slice":authority,"worker":worker or worker_incarnation()}
        atomic_json(lifecycle,state)
        return True
    finally:
        _release_lifecycle_lock(handle)

def advance_grant_stage(grant_path:Path,stage:str,**evidence:Any)->bool:
    """Durably record execution progress without clobbering another grant."""
    lifecycle=lifecycle_path_for_grant(grant_path)
    try:
        grant_id=json.loads(grant_path.read_text(encoding="utf-8"))["grant_id"]
    except (OSError,json.JSONDecodeError,KeyError,TypeError):
        return False
    def update(record):
        if record.get("state")!="CLAIMED":
            return False
        record["execution_stage"]=stage
        record["stage_updated_at"]=datetime.now(UTC).isoformat()
        for key,value in evidence.items():
            if value not in (None,""):
                record[key]=value
        return True
    return mutate_grant_lifecycle(lifecycle,grant_id,update)

def start_grant_heartbeat(grant_path:Path,worker:dict[str,Any])->tuple[threading.Event,threading.Thread]:
    stop=threading.Event()
    thread=threading.Thread(target=heartbeat_grant,args=(grant_path,stop),kwargs={"worker":worker},daemon=True)
    thread.start()
    return stop,thread

def select_repo(state_path:Path,eligible_products:set[str]|None=None,eligible_repos:set[str]|None=None)->str|None:
    prior_repo=None
    prior_index=-1
    if state_path.exists():
        try:
            previous=json.loads(state_path.read_text(encoding="utf-8"))
            prior_repo=previous.get("last_repository")
            prior_index=int(previous.get("last_repo_index",-1))
        except Exception:
            prior_repo=None
            prior_index=-1
    if eligible_repos is not None:
        repo_universe=tuple(sorted(eligible_repos))
    elif eligible_products is None:
        repo_universe=portfolio_repositories()
    else:
        products=contract_products()
        allowed={repo for product in eligible_products for repo in products.get(product,())}
        repo_universe=tuple(repo for repo in portfolio_repositories() if repo in allowed)
    if not repo_universe:
        return None
    if prior_repo in repo_universe:
        prior=repo_universe.index(prior_repo)
    elif prior_repo is not None:
        prior=-1
    else:
        # One-way compatibility for state written before repository identity was persisted.
        prior=prior_index if 0<=prior_index<len(repo_universe) else -1
    for offset in range(1,len(repo_universe)+1):
        idx=(prior+offset)%len(repo_universe)
        repo=repo_universe[idx]
        if lane_capacity(repo)>0:
            return repo
    return None

def prior_failure_experience(state_path:Path,limit:int=5)->str:
    """Return bounded recurring failure experience; evidence only, never authority."""
    path=state_path.with_name("autonomous-failure-lessons.json")
    try:
        lessons=json.loads(path.read_text(encoding="utf-8")).get("lessons",{})
    except (OSError,json.JSONDecodeError,TypeError):
        return "none recorded"
    ranked=sorted(lessons.values(),key=lambda x:(int(x.get("occurrences",0)),x.get("last_seen","")),reverse=True)
    lines=[]
    for item in ranked[:limit]:
        detail=" ".join(str(item.get("detail","")).split())[:600]
        repairs=item.get("verified_repairs",{})
        repair_text=""
        if repairs:
            best=max(repairs.values(),key=lambda r:int(r.get("verified_successes",0)))
            repair_text=f" verified_repair={best.get('strategy','unknown')} successes={best.get('verified_successes',0)}"
        lines.append(f"- [{item.get('fingerprint','unknown')}] stage={item.get('stage','unknown')} occurrences={item.get('occurrences',0)} outcome={item.get('last_outcome','unknown')}{repair_text}: {detail}")
    return "\n".join(lines) if lines else "none recorded"

def problem_solving_guidance()->str:
    path=Path(__file__).resolve().parents[1]/"docs"/"AUTONOMOUS_TROUBLESHOOTING.md"
    try:
        return path.read_text(encoding="utf-8")[:12000]
    except OSError:
        return ""

def prompt_for(repo:str,state_path:Path|None=None)->str:
    owned=open_pr_paths(repo)
    avoid=sorted({p for lease in owned for p in lease.paths})
    avoid_text=", ".join(avoid[:80]) if avoid else "none"
    return f"""You are the autonomous laptop builder for {repo}.
{WORKER_PROMPT_VOICE}
Read the repository's AGENTS.md, README, roadmap/milestone, architecture, and engineering/control-plane files before editing.
Choose exactly ONE small, high-value, authorized P4/P5 slice that is independent of Product Reality and does not require a human decision.
Open PRs already own these paths: {avoid_text}

Troubleshooting and engineering practice guidance:
{problem_solving_guidance()}

Prior recurring failure experience (evidence, not permission to bypass gates):
{prior_failure_experience(state_path) if state_path is not None else "none recorded"}

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

def slice_path_failure(paths:list[str],authority:dict[str,Any]|None)->str|None:
    """Enforce bounded slice path authority independently of worker prompt compliance."""
    if authority is None:
        return None
    allowed=authority.get("allowed_paths",[])
    forbidden=authority.get("forbidden_paths",[])
    if not isinstance(allowed,list) or not isinstance(forbidden,list):
        return "invalid_slice_path_authority"
    def normalize(value:str)->str|None:
        if not isinstance(value,str) or not value.strip():
            return None
        value=value.replace(chr(92),"/").strip()
        if value.startswith("/") or ".." in value.split("/"):
            return None
        return value.rstrip("/")
    allowed_n=[normalize(x) for x in allowed]
    forbidden_n=[normalize(x) for x in forbidden]
    if any(x is None for x in allowed_n+forbidden_n):
        return "invalid_slice_path_authority"
    def matches(path:str,prefix:str)->bool:
        path=path.replace(chr(92),"/").strip("/")
        return path==prefix or path.startswith(prefix+"/")
    for path in paths:
        if any(matches(path,prefix) for prefix in forbidden_n):
            return f"slice_forbidden_path:{path}"
        if allowed_n and not any(matches(path,prefix) for prefix in allowed_n):
            return f"slice_path_outside_authority:{path}"
    return None

def persisted_change_failure(ws:Path,base:str,paths:list[str])->str|None:
    """Read worker output back from disk and prove changed Python artifacts are structurally valid."""
    for rel in paths:
        path=ws/rel
        if not path.is_file() or path.is_symlink():
            continue
        try:
            data=path.read_bytes()
        except OSError:
            return f"persisted_artifact_unreadable:{rel}"
        if rel.endswith(".py"):
            try:
                compile(data,rel,"exec")
            except (SyntaxError,ValueError,TypeError):
                return f"persisted_python_invalid:{rel}"
    diff=run(["git","diff","--binary",base,"--",*paths],ws)
    if diff.returncode:
        return "persisted_diff_unreadable"
    if not diff.stdout.strip():
        return "persisted_change_missing"
    return None

def generated_change_failure(ws:Path,base:str,paths:list[str],slice_authority:dict[str,Any]|None=None)->str|None:
    persisted=persisted_change_failure(ws,base,paths)
    if persisted:
        return persisted
    bounded=slice_path_failure(paths,slice_authority)
    if bounded:
        return bounded
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
    universe=portfolio_repositories()
    idx=universe.index(repo) if repo in universe else -1
    if dry_run:
        result={"status":"DRY_RUN","repository":repo,"last_repo_index":idx,"last_repository":repo}
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
        slice_authority=execution_slice_authority(execution_grant,repo,expected)
        if slice_authority is None:
            return {"status":"EXECUTION_SLICE_AUTHORITY_REQUIRED","repository":repo,"expected_sha":expected}
        worker=worker_incarnation()
        if not claim_execution_grant(execution_grant,repo,expected,worker=worker):
            return {"status":"EXECUTION_GRANT_CLAIM_FAILED","repository":repo}
        heartbeat_stop,heartbeat_thread=start_grant_heartbeat(execution_grant,worker)
        advance_grant_stage(execution_grant,"BASE_VERIFIED",repository=repo,base_sha=base,
                            capability_id=slice_authority["capability_id"],slice_id=slice_authority["slice_id"])
    else:
        slice_authority=None
        heartbeat_stop=heartbeat_thread=None
    stamp=datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    branch=f"actor/autonomous-{repo.split('/')[-1].lower()}-{stamp}"
    if run(["git","checkout","-B",branch,base],ws).returncode:
        raise RuntimeError("branch creation failed")
    if execution_grant is not None:
        advance_grant_stage(execution_grant,"BRANCH_CREATED",repository=repo,base_sha=base,branch=branch)

    task_prompt=prompt_for_slice(repo,slice_authority,state_path) if slice_authority is not None else prompt_for(repo,state_path)
    aider=run_aider(ws,task_prompt)
    cleanup_aider_artifacts(ws)
    paths=changed_paths(ws,base)
    if aider.returncode!=0:
        reset_workspace(ws,base)
        lesson=retain_failure_lesson(state_path,"aider_execution",aider.stderr or aider.stdout or f"returncode={aider.returncode}","BUILDER_FAILED")
        result={"status":"BUILDER_FAILED","repository":repo,"returncode":aider.returncode,"failure_fingerprint":lesson["fingerprint"],"last_repo_index":idx,"last_repository":repo}
        atomic_json(state_path,result); return result
    if not paths:
        result={"status":"NO_OP","repository":repo,"last_repo_index":idx,"last_repository":repo}
        atomic_json(state_path,result); return result
    initial_failure=generated_change_failure(ws,base,paths,slice_authority)
    if initial_failure:
        repair=run_aider(ws,repair_prompt(repo,initial_failure,paths))
        cleanup_aider_artifacts(ws)
        paths=changed_paths(ws,base)
        if repair.returncode!=0:
            reset_workspace(ws,base)
            result={"status":"AUTONOMOUS_CHANGE_REJECTED","repository":repo,
                    "initial_failure":initial_failure,"final_failure":"repair_execution_failed",
                    "repair_attempted":True,"last_repo_index":idx,"last_repository":repo}
            atomic_json(state_path,result); return result
        if not paths:
            result={"status":"SELF_REPAIRED_TO_NO_OP","repository":repo,
                    "initial_failure":initial_failure,"repair_attempted":True,"last_repo_index":idx,"last_repository":repo}
            atomic_json(state_path,result); return result
        final_failure=generated_change_failure(ws,base,paths,slice_authority)
        if final_failure:
            reset_workspace(ws,base)
            result={"status":"AUTONOMOUS_CHANGE_REJECTED","repository":repo,
                    "initial_failure":initial_failure,"final_failure":final_failure,
                    "repair_attempted":True,"last_repo_index":idx,"last_repository":repo}
            atomic_json(state_path,result); return result
    preflight=run_preflight(ws)
    if preflight.returncode!=0:
        reset_workspace(ws,base)
        lesson=retain_failure_lesson(state_path,"preflight",preflight.stderr or preflight.stdout or f"returncode={preflight.returncode}","PREFLIGHT_FAILED")
        result={"status":"PREFLIGHT_FAILED","repository":repo,"returncode":preflight.returncode,"failure_fingerprint":lesson["fingerprint"],"last_repo_index":idx,"last_repository":repo}
        atomic_json(state_path,result); return result
    run(["git","add","-A"],ws)
    commit=run(["git","commit","-m",f"actor autonomous bounded slice {stamp}"],ws)
    if commit.returncode:
        reset_workspace(ws,base)
        result={"status":"COMMIT_FAILED","repository":repo,"last_repo_index":idx,"last_repository":repo}
        atomic_json(state_path,result); return result
    head=run(["git","rev-parse","HEAD"],ws).stdout.strip()
    if execution_grant is not None:
        advance_grant_stage(execution_grant,"COMMITTED",repository=repo,base_sha=base,branch=branch,head_sha=head)
    push=run(["git","push","-u","origin",branch],ws)
    if push.returncode:
        result={"status":"PUSH_FAILED","repository":repo,"head":head,"last_repo_index":idx,"last_repository":repo}
        atomic_json(state_path,result); return result
    if execution_grant is not None:
        advance_grant_stage(execution_grant,"PUSHED",repository=repo,base_sha=base,branch=branch,head_sha=head,remote_head_sha=head)
    linear_id=os.environ.get("EXPONENTIAL_LINEAR_ID","").strip()
    linear_ref=f" Linear: {linear_id}." if linear_id else ""
    body=("Autonomous laptop-actor bounded slice. Local Ollama/Aider produced the change; "
          "canonical exact-head preflight passed. Merge authority remains external and fail-closed."
          +linear_ref)
    created=subprocess.run(["gh","pr","create","--repo",repo,"--base",main,"--head",branch,
                            "--title","AUTO: bounded autonomous engineering slice","--body",body],
                           cwd=ws,text=True,capture_output=True,check=False)
    status="PR_OPEN" if created.returncode==0 else "PR_CREATE_FAILED"
    pr_url=created.stdout.strip()
    pr_number=pr_number_from_url(pr_url) if created.returncode==0 else None
    if created.returncode==0 and pr_number is None:
        status="PR_IDENTITY_UNPROVEN"
    if execution_grant is not None and status=="PR_OPEN":
        advance_grant_stage(execution_grant,"PR_OPEN",repository=repo,base_sha=base,branch=branch,head_sha=head,remote_head_sha=head,
                            pr_url=pr_url,pr_number=pr_number)
    result={"status":status,"repository":repo,"branch":branch,"base_sha":base,
            "head_sha":head,"changed_paths":paths,"pr_url":pr_url,"pr_number":pr_number,
            "last_repo_index":idx,"last_repository":repo}
    atomic_json(state_path,result)
    return result

def pr_number_from_url(value:str)->int|None:
    """Extract durable PR identity from the canonical gh create output."""
    try:
        marker="/pull/"
        if marker not in value:
            return None
        number=value.strip().rstrip("/").rsplit(marker,1)[1]
        return int(number) if number.isdigit() else None
    except (TypeError,ValueError):
        return None

def stop_grant_heartbeat(stop:threading.Event|None,thread:threading.Thread|None)->None:
    if stop is not None:
        stop.set()
    if thread is not None:
        thread.join(timeout=2)

def finish_grant_lifecycle(grant_path:Path,result:dict[str,Any])->None:
    lifecycle=lifecycle_path_for_grant(grant_path)
    try:
        grant_id=json.loads(grant_path.read_text(encoding="utf-8"))["grant_id"]
    except (OSError,json.JSONDecodeError,KeyError,TypeError):
        return
    def update(record):
        record["state"]="COMPLETED" if result.get("status") in {
            "PR_OPEN","NO_OP","SELF_REPAIRED_TO_NO_OP","DRY_RUN","NO_ELIGIBLE_REPOSITORY"
        } else "FAILED"
        record["finished_at"]=datetime.now(UTC).isoformat()
        record["result_status"]=result.get("status","UNKNOWN")
        for key in ("repository","branch","base_sha","head_sha"):
            if result.get(key):
                record[key]=result[key]
        return True
    mutate_grant_lifecycle(lifecycle,grant_id,update)

def main()->int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--workspace-root",required=True)
    parser.add_argument("--state",required=True)
    parser.add_argument("--dry-run",action="store_true")
    parser.add_argument("--eligible-products",default=None,
                        help="Manual/dev product filter; requires --unsafe-manual-all")
    parser.add_argument("--execution-grant",type=Path,
                        help="Exact-head autonomous execution grant")
    parser.add_argument("--unsafe-manual-all",action="store_true",
                        help="Explicit manual/dev escape hatch for running without an execution grant")
    parser.add_argument("--invocation-id",default=None,
                        help="Supervisor-issued identity used to reject stale builder state")
    args=parser.parse_args()
    state=Path(args.state)
    invocation_id=args.invocation_id or uuid.uuid4().hex
    try:
        workspace=Path(args.workspace_root)
        if not args.execution_grant and not args.unsafe_manual_all:
            result={"status":"EXECUTION_GRANT_REQUIRED","invocation_id":invocation_id}
            atomic_json(state,result)
            print(json.dumps(result,indent=2,sort_keys=True))
            return 1
        if args.execution_grant:
            eligible_repos=granted_repositories(args.execution_grant,workspace)
            eligible=None
        else:
            eligible_repos=None
            eligible=None if args.eligible_products is None else {p for p in args.eligible_products.split(",") if p}
        result=build_once(workspace,state,args.dry_run,eligible,eligible_repos,args.execution_grant)
        result={**result,"invocation_id":invocation_id}
        atomic_json(state,result)
        if args.execution_grant:
            finish_grant_lifecycle(args.execution_grant,result)
    except (OSError,RuntimeError,subprocess.TimeoutExpired,json.JSONDecodeError) as exc:
        result={"status":"P1_BUILDER_FAILURE","error_class":type(exc).__name__,"error":str(exc)[:500],
                "invocation_id":invocation_id}
        atomic_json(state,result)
    print(json.dumps(result,indent=2,sort_keys=True))
    return 0 if result["status"] not in {"P1_BUILDER_FAILURE","BUILDER_FAILED"} else 1

if __name__=="__main__":
    raise SystemExit(main())
