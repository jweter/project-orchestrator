"""Idle-gated autonomous bounded-slice builder using local Ollama + Aider."""
from __future__ import annotations
import argparse, ctypes, hashlib, json, os, shutil, socket, subprocess, sys, threading, time, uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from project_orchestrator.execution_runtime import DEFAULT_EXECUTION_RUNTIMES, RuntimeRequest
from project_orchestrator.runtime_leases import RuntimeLeasePool, DEFAULT_RUNTIME_CAPACITIES
from project_orchestrator.independent_verdict import review_candidate, bind_preflight_evidence
from project_orchestrator.grant_lifecycle import (
    acquire_lifecycle_lock as _acquire_lifecycle_lock,
    grant_state_root,
    lifecycle_path_for_grant,
    mutate_grant_lifecycle,
    release_lifecycle_lock as _release_lifecycle_lock,
)
from project_orchestrator.state_io import atomic_json
from project_orchestrator.builder_promotion import promote_candidate

ACTIVE_RUNTIME_ID="native-windows"
ACTIVE_RUNTIME_FENCE=None
ACTIVE_RESOURCE_FENCES=[]
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
    """Execute through Foundry's runtime boundary while preserving legacy result shape."""
    runtime_id=kwargs.pop("runtime_id",ACTIVE_RUNTIME_ID)
    if ACTIVE_RUNTIME_FENCE is not None:
        pool,worker_id,lease_id=ACTIVE_RUNTIME_FENCE
        pool.assert_fence(worker_id,lease_id)
        pool.heartbeat(worker_id,lease_id)
        for resource_worker,resource_lease_id in ACTIVE_RESOURCE_FENCES:
            pool.assert_fence(resource_worker,resource_lease_id)
            pool.heartbeat(resource_worker,resource_lease_id)
    env=kwargs.pop("env",None)
    timeout=kwargs.pop("timeout",None)
    if kwargs:
        raise TypeError(f"unsupported runtime options: {sorted(kwargs)}")
    request=RuntimeRequest(tuple(argv),cwd,env,timeout)
    runtime=DEFAULT_EXECUTION_RUNTIMES.require(runtime_id)
    if ACTIVE_RUNTIME_FENCE is None:
        result=runtime.execute(request)
    else:
        pool,worker_id,lease_id=ACTIVE_RUNTIME_FENCE
        def authority_check():
            pool.assert_fence(worker_id,lease_id)
            pool.heartbeat(worker_id,lease_id)
            for resource_worker,resource_lease_id in ACTIVE_RESOURCE_FENCES:
                pool.assert_fence(resource_worker,resource_lease_id)
                pool.heartbeat(resource_worker,resource_lease_id)
        supervised=getattr(runtime,"execute_supervised",None)
        if not callable(supervised):
            raise RuntimeError("EXECUTION_RUNTIME_NOT_FENCEABLE")
        result=supervised(request,authority_check)
    return subprocess.CompletedProcess(argv,result.returncode,result.stdout,result.stderr)

def gh_json(args:list[str],cwd:Path|None=None)->Any:
    p=run(["gh",*args],cwd or Path.cwd())
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

def recover_dirty_worker_workspace(ws:Path,root:Path,worker_id:str,name:str)->Path:
    """Preserve residue from a disposable worker worktree, then return it clean."""
    recovery=root/".worker-recovery"/worker_id/name
    recovery.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    patch=run(["git","diff","--binary","HEAD"],ws)
    (recovery/f"{stamp}.patch").write_text(patch.stdout or "",encoding="utf-8")
    untracked=run(["git","ls-files","--others","--exclude-standard"],ws)
    saved=[]
    if untracked.returncode==0:
        files=recovery/f"{stamp}-untracked"
        for rel in (line.strip() for line in untracked.stdout.splitlines() if line.strip()):
            source=ws/rel
            if source.is_file():
                target=files/rel
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(source,target)
                saved.append(rel)
    atomic_json(recovery/f"{stamp}.json",{
        "schema_version":1,"worker_id":worker_id,"workspace":str(ws),
        "recorded_at_utc":datetime.now(UTC).isoformat(),"untracked":saved,
    })
    reset=run(["git","reset","--hard","HEAD"],ws)
    clean=run(["git","clean","-fd"],ws)
    if reset.returncode or clean.returncode:
        raise RuntimeError("worker workspace recovery failed")
    return recovery

def ensure_workspace(repo:str,root:Path,worker_id:str|None=None)->Path:
    name=repo.split("/",1)[1]
    canonical=root/name
    ws=canonical
    if worker_id and canonical.exists():
        isolated=root/".worker-worktrees"/worker_id/name
        isolated.parent.mkdir(parents=True,exist_ok=True)
        if not isolated.exists():
            # A detached worktree gives each worker an independent index and
            # working tree while sharing Git objects with the canonical clone.
            proc=run(["git","worktree","add","--detach",str(isolated),"origin/main"],canonical)
            if proc.returncode:
                raise RuntimeError("worker worktree creation failed")
        ws=isolated
    if not ws.exists():
        root.mkdir(parents=True,exist_ok=True)
        proc=run(["gh","repo","clone",repo,str(ws),"--","--filter=blob:none"],root)
        if proc.returncode: raise RuntimeError("clone failed")
    origin=run(["git","remote","get-url","origin"],ws)
    if origin.returncode or repo.lower() not in origin.stdout.lower():
        raise RuntimeError("origin mismatch")
    clean_generated_workspace_artifacts(ws)
    dirty=run(["git","status","--porcelain"],ws)
    if dirty.returncode:
        raise RuntimeError("workspace status failed")
    if dirty.stdout.strip():
        isolated_worker=bool(worker_id) and ws != canonical and ".worker-worktrees" in ws.parts
        if not isolated_worker:
            raise RuntimeError("workspace dirty")
        recover_dirty_worker_workspace(ws,root,worker_id,name)
        verified=run(["git","status","--porcelain"],ws)
        if verified.returncode or verified.stdout.strip():
            raise RuntimeError("worker workspace recovery left residue")
    fetch=run(["git","fetch","--prune","origin"],ws)
    if fetch.returncode: raise RuntimeError("fetch failed")
    return ws

HEARTBEAT_SECONDS=60

def _windows_process_start_fingerprint(pid:int)->str|None:
    """Return creation identity only while the Windows process is still active."""
    try:
        import ctypes
        from ctypes import wintypes
        kernel32=ctypes.WinDLL("kernel32",use_last_error=True)
        handle=kernel32.OpenProcess(0x1000,False,pid)
        if not handle:
            return None
        creation=wintypes.FILETIME(); exit_time=wintypes.FILETIME()
        kernel=wintypes.FILETIME(); user=wintypes.FILETIME()
        exit_code=wintypes.DWORD()
        try:
            if not kernel32.GetExitCodeProcess(handle,ctypes.byref(exit_code)):
                return None
            if exit_code.value != 259:  # STILL_ACTIVE
                return None
            if not kernel32.GetProcessTimes(handle,ctypes.byref(creation),ctypes.byref(exit_time),ctypes.byref(kernel),ctypes.byref(user)):
                return None
            ticks=(creation.dwHighDateTime<<32)|creation.dwLowDateTime
            return f"windows:{ticks}"
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError,OSError):
        return None

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
        return _windows_process_start_fingerprint(pid)
    return None

def worker_incarnation()->dict[str,Any]:
    """Identify this concrete worker process; PID alone is intentionally insufficient."""
    return {"host":socket.gethostname(),"pid":os.getpid(),
            "incarnation_id":uuid.uuid4().hex,
            "process_start_fingerprint":process_start_fingerprint(),
            "lease_token":uuid.uuid4().hex}

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
    if (payload.get("schema_version") not in {4,5} or not isinstance(grant_id,str) or not grant_id
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

def grant_execution_identity(path:Path)->tuple[str,str]|None:
    """Return immutable runner/runtime authority from a v5 execution grant."""
    try:
        grant=json.loads(path.read_text(encoding="utf-8"))
        authority=grant["execution_slice"]
        runner=authority["runner_id"]; runtime=authority["execution_runtime"]
    except (OSError,json.JSONDecodeError,KeyError,TypeError):
        return None
    if grant.get("schema_version",0)<5 or not isinstance(runner,str) or not runner or not isinstance(runtime,str) or not runtime:
        return None
    return runner,runtime

def execution_identity_matches(path:Path,runner_id:str,runtime_id:str)->bool:
    return grant_execution_identity(path)==(runner_id,runtime_id)

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
    engineering_lessons=[
        item for item in lessons.values()
        if item.get("last_outcome")!="RUNNER_CAPACITY_DEFERRED"
    ]
    ranked=sorted(engineering_lessons,key=lambda x:(int(x.get("occurrences",0)),x.get("last_seen","")),reverse=True)
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
def _ollama_lock_dir()->Path:
    return Path(os.environ.get(
        "EXPONENTIAL_OLLAMA_LOCK_DIR",
        str(Path.home()/".exponential-engine"/"locks"/"ollama-aider"),
    ))

def _acquire_ollama_lock()->Path:
    lock=_ollama_lock_dir()
    lock.parent.mkdir(parents=True,exist_ok=True)
    deadline=time.monotonic()+MAX_AIDER_SECONDS
    while True:
        try:
            lock.mkdir()
            (lock/"owner").write_text(f"{os.getpid()}\n{time.time()}\n",encoding="utf-8")
            return lock
        except FileExistsError:
            try:
                owner_pid=int((lock/"owner").read_text(encoding="utf-8").splitlines()[0])
                if process_start_fingerprint(owner_pid) is None:
                    shutil.rmtree(lock,ignore_errors=True)
                    continue
            except (FileNotFoundError,ValueError,IndexError):
                # mkdir() publishes the lease before its owner metadata can be written.
                # A contender must not steal a freshly-created lease while that tiny
                # publication window is still open. Only metadata that remains
                # unreadable past the stale-publication grace period is recoverable.
                try:
                    stale=time.time()-lock.stat().st_mtime>5
                except FileNotFoundError:
                    stale=False
                if stale:
                    shutil.rmtree(lock,ignore_errors=True)
                    continue
            if time.monotonic()>=deadline:
                raise TimeoutError("timed out waiting for exclusive local Ollama builder slot")
            time.sleep(1)

def run_aider(ws:Path,message:str)->subprocess.CompletedProcess[str]:
    env=os.environ.copy()
    env.setdefault("OLLAMA_API_BASE","http://127.0.0.1:11434")
    argv=["aider","--model",MODEL,"--message",message,
          "--no-auto-commits","--no-gitignore","--no-pretty","--no-stream",
          "--analytics-disable","--no-check-update","--no-show-model-warnings",
          "--no-suggest-shell-commands","--yes-always"]
    lock=_acquire_ollama_lock()
    try:
        return run(argv,ws,env=env,timeout=MAX_AIDER_SECONDS)
    finally:
        shutil.rmtree(lock,ignore_errors=True)

def run_claude_code(ws:Path,message:str)->subprocess.CompletedProcess[str]:
    """Independent authenticated coding lane; worktree and grant remain the sandbox boundary."""
    argv=["claude","-p","--output-format","text","--permission-mode","acceptEdits",message]
    return run(argv,ws,timeout=MAX_AIDER_SECONDS)

def run_builder(ws:Path,message:str,runner_id:str|None)->subprocess.CompletedProcess[str]:
    if runner_id=="claude-code":
        return run_claude_code(ws,message)
    if runner_id in {None,"aider-ollama"}:
        return run_aider(ws,message)
    raise RuntimeError(f"UNSUPPORTED_RUNNER:{runner_id}")

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

def run_exact_head_preflight(ws:Path,expected_head:str)->tuple[subprocess.CompletedProcess[str],str,bool]:
    """Run canonical preflight only while the worktree remains at the committed candidate HEAD."""
    before=run(["git","rev-parse","HEAD"],ws).stdout.strip()
    if not expected_head or before!=expected_head:
        result=subprocess.CompletedProcess(
            ["canonical-preflight"],1,"",f"PREFLIGHT_HEAD_MISMATCH:{expected_head}:{before}"
        )
        return result,before,False
    result=run_preflight(ws)
    after=run(["git","rev-parse","HEAD"],ws).stdout.strip()
    return result,after,after==expected_head

def refresh_base_before_publish(ws:Path,main_branch:str,expected_base:str)->dict[str,str]:
    """Refresh and reconcile the unpublished candidate with the live default branch.

    The autonomous worker must never open a PR from a base known to be stale. A
    successful rebase invalidates prior exact-head verification, so callers must
    rerun canonical preflight and independent review before publishing.
    """
    fetched=run(["git","fetch","origin",main_branch],ws)
    if fetched.returncode:
        head=run(["git","rev-parse","HEAD"],ws).stdout.strip()
        return {"status":"FETCH_FAILED","base_sha":expected_base,"head_sha":head}
    latest_result=run(["git","rev-parse",f"origin/{main_branch}"],ws)
    latest_base=latest_result.stdout.strip() if latest_result.returncode==0 else ""
    head=run(["git","rev-parse","HEAD"],ws).stdout.strip()
    if not latest_base:
        return {"status":"FETCH_FAILED","base_sha":expected_base,"head_sha":head}
    if latest_base==expected_base:
        return {"status":"UNCHANGED","base_sha":expected_base,"head_sha":head}
    rebased=run(["git","rebase",latest_base],ws)
    if rebased.returncode:
        run(["git","rebase","--abort"],ws)
        return {"status":"CONFLICT","base_sha":latest_base,"head_sha":head}
    new_head=run(["git","rev-parse","HEAD"],ws).stdout.strip()
    return {"status":"REBASED","base_sha":latest_base,"head_sha":new_head}


def build_once(workspace_root:Path,state_path:Path,dry_run:bool=False,eligible_products:set[str]|None=None,eligible_repos:set[str]|None=None,
               execution_grant:Path|None=None,worker_id:str|None=None)->dict[str,Any]:
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

    ws=ensure_workspace(repo,workspace_root,worker_id)
    main="main"
    if execution_grant is not None:
        if not isinstance(eligible_repos,dict):
            return {"status":"EXECUTION_GRANT_INVALID","repository":repo}
        expected=eligible_repos.get(repo)
        if not expected:
            return {"status":"EXECUTION_GRANT_INVALID","repository":repo}
        checkout_target=expected
        pre_checkout=run(["git","status","--porcelain"],ws)
        porcelain_states=set(" MADRCU?!")
        dirty_lines=[line for line in pre_checkout.stdout.splitlines()
                     if len(line)>=3 and line[0] in porcelain_states and line[1] in porcelain_states]
        if pre_checkout.returncode or dirty_lines:
            raise RuntimeError("WORKSPACE_CHANGED_AFTER_ADMISSION")
        reset=run(["git","checkout","--detach",checkout_target],ws)
    else:
        expected=None
        checkout_target=f"origin/{main}"
        staging_branch="actor-staging" if not worker_id else f"actor-staging-{worker_id}"
        reset=run(["git","checkout","-B",staging_branch,checkout_target],ws)
    if reset.returncode: raise RuntimeError("base checkout failed")
    base=run(["git","rev-parse","HEAD"],ws).stdout.strip()
    if execution_grant is not None:
        if base!=expected:
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
    selected_runner=(slice_authority or {}).get("runner_id") if slice_authority is not None else None
    execution_started_at=datetime.now(UTC)
    if execution_grant is not None:
        advance_grant_stage(execution_grant,"EXECUTING",execution_started_at=execution_started_at.isoformat())
    builder=run_builder(ws,task_prompt,selected_runner)
    execution_finished_at=datetime.now(UTC)
    if execution_grant is not None:
        advance_grant_stage(execution_grant,"EXECUTION_FINISHED",
                            execution_started_at=execution_started_at.isoformat(),
                            execution_finished_at=execution_finished_at.isoformat(),
                            execution_seconds=max((execution_finished_at-execution_started_at).total_seconds(),0.0))
    cleanup_aider_artifacts(ws)
    paths=changed_paths(ws,base)
    if builder.returncode!=0:
        reset_workspace(ws,base)
        detail=builder.stderr or builder.stdout or f"returncode={builder.returncode}"
        capacity_limited=selected_runner=="claude-code" and any(
            marker in detail.lower() for marker in ("session limit","usage limit","rate limit")
        )
        outcome="RUNNER_CAPACITY_DEFERRED" if capacity_limited else "BUILDER_FAILED"
        result={"status":outcome,"repository":repo,"runner_id":selected_runner,
                "returncode":builder.returncode,"last_repo_index":idx,"last_repository":repo}
        if not capacity_limited:
            lesson=retain_failure_lesson(
                state_path,f"{selected_runner or 'aider-ollama'}_execution",detail,outcome
            )
            result["failure_fingerprint"]=lesson["fingerprint"]
        atomic_json(state_path,result); return result
    if not paths:
        result={"status":"NO_OP","repository":repo,"last_repo_index":idx,"last_repository":repo}
        atomic_json(state_path,result); return result
    initial_failure=generated_change_failure(ws,base,paths,slice_authority)
    if initial_failure:
        repair_started_at=datetime.now(UTC)
        repair=run_builder(ws,repair_prompt(repo,initial_failure,paths),selected_runner)
        repair_finished_at=datetime.now(UTC)
        repair_seconds=max((repair_finished_at-repair_started_at).total_seconds(),0.0)
        if execution_grant is not None:
            advance_grant_stage(execution_grant,"REPAIR_FINISHED",
                                repair_attempts=1,repair_seconds=repair_seconds,
                                execution_seconds=max((execution_finished_at-execution_started_at).total_seconds(),0.0)+repair_seconds)
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
    run(["git","add","-A"],ws)
    commit=run(["git","commit","-m",f"actor autonomous bounded slice {stamp}"],ws)
    if commit.returncode:
        reset_workspace(ws,base)
        result={"status":"COMMIT_FAILED","repository":repo,"last_repo_index":idx,"last_repository":repo}
        atomic_json(state_path,result); return result
    head=run(["git","rev-parse","HEAD"],ws).stdout.strip()
    return promote_candidate(
        ws=ws,
        state_path=state_path,
        repo=repo,
        idx=idx,
        main_branch=main,
        branch=branch,
        base=base,
        head=head,
        paths=paths,
        slice_authority=slice_authority,
        execution_grant=execution_grant,
        run_exact_head_preflight_fn=run_exact_head_preflight,
        reset_workspace_fn=reset_workspace,
        retain_failure_lesson_fn=retain_failure_lesson,
        bind_preflight_evidence_fn=bind_preflight_evidence,
        review_candidate_fn=review_candidate,
        advance_grant_stage_fn=advance_grant_stage,
        refresh_base_before_publish_fn=refresh_base_before_publish,
        changed_paths_fn=changed_paths,
        generated_change_failure_fn=generated_change_failure,
        run_fn=run,
        pr_number_from_url_fn=pr_number_from_url,
    )

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
        result_status=result.get("status","UNKNOWN")
        if result_status in {"USER_ACTIVE","RUNNER_CAPACITY_DEFERRED"}:
            # Execution was intentionally deferred; no engineering failure
            # occurred and a later grant may retry the same slice.
            record["state"]="DEFERRED"
        elif result_status in {
            "PR_OPEN","NO_OP","SELF_REPAIRED_TO_NO_OP","DRY_RUN","NO_ELIGIBLE_REPOSITORY"
        }:
            record["state"]="COMPLETED"
        else:
            record["state"]="FAILED"
        record["finished_at"]=datetime.now(UTC).isoformat()
        record["result_status"]=result_status
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
    parser.add_argument("--runner-id",default=None,
                        help="Runner identity authorized by the execution grant")
    parser.add_argument("--runtime-id",default=None,
                        help="Execution runtime identity authorized by the execution grant")
    parser.add_argument("--runtime-lease-id",default=None,
                        help="Current runtime lease fencing token")
    parser.add_argument("--runtime-lease-state",default=None,
                        help="Durable runtime lease authority state")
    parser.add_argument("--resource-lease-ids",default="{}",
                        help="JSON resource-id to fencing-token authority")
    args=parser.parse_args()
    state=Path(args.state)
    invocation_id=args.invocation_id or uuid.uuid4().hex
    worker_identity=invocation_id
    if args.execution_grant:
        try:
            worker_identity=json.loads(Path(args.execution_grant).read_text(encoding="utf-8")).get("worker_id") or invocation_id
        except (OSError,json.JSONDecodeError,TypeError):
            worker_identity=invocation_id
    try:
        global ACTIVE_RUNTIME_ID,ACTIVE_RUNTIME_FENCE,ACTIVE_RESOURCE_FENCES
        ACTIVE_RUNTIME_ID=args.runtime_id or "native-windows"
        if args.runtime_lease_id:
            if not args.runtime_lease_state:
                raise RuntimeError("RUNTIME_LEASE_STATE_REQUIRED")
            lease_pool=RuntimeLeasePool(DEFAULT_RUNTIME_CAPACITIES,Path(args.runtime_lease_state))
            ACTIVE_RUNTIME_FENCE=(lease_pool,worker_identity,args.runtime_lease_id)
            lease_pool.assert_fence(worker_identity,args.runtime_lease_id)
            try:
                resource_ids=json.loads(args.resource_lease_ids)
            except (json.JSONDecodeError,TypeError):
                raise RuntimeError("RESOURCE_LEASE_AUTHORITY_INVALID")
            if not isinstance(resource_ids,dict):
                raise RuntimeError("RESOURCE_LEASE_AUTHORITY_INVALID")
            ACTIVE_RESOURCE_FENCES=[
                (f"{worker_identity}:{resource_id}",lease_id)
                for resource_id,lease_id in resource_ids.items()
            ]
            for resource_worker,resource_lease_id in ACTIVE_RESOURCE_FENCES:
                lease_pool.assert_fence(resource_worker,resource_lease_id)
        workspace=Path(args.workspace_root)
        if not args.execution_grant and not args.unsafe_manual_all:
            result={"status":"EXECUTION_GRANT_REQUIRED","invocation_id":invocation_id}
            atomic_json(state,result)
            print(json.dumps(result,indent=2,sort_keys=True))
            return 1
        if args.execution_grant:
            if not args.runner_id or not args.runtime_id or not execution_identity_matches(
                    args.execution_grant,args.runner_id,args.runtime_id):
                result={"status":"EXECUTION_IDENTITY_MISMATCH","invocation_id":invocation_id,
                        "runner_id":args.runner_id,"runtime_id":args.runtime_id}
                atomic_json(state,result)
                print(json.dumps(result,indent=2,sort_keys=True))
                return 1
            eligible_repos=granted_repositories(args.execution_grant,workspace)
            try:
                granted_slice=json.loads(args.execution_grant.read_text(encoding="utf-8")).get("execution_slice",{})
                target_repo=granted_slice.get("repository") if isinstance(granted_slice,dict) else None
            except (OSError,json.JSONDecodeError,TypeError):
                target_repo=None
            eligible_repos={target_repo:eligible_repos[target_repo]} if target_repo in eligible_repos else {}
            eligible=None
        else:
            eligible_repos=None
            eligible=None if args.eligible_products is None else {p for p in args.eligible_products.split(",") if p}
        result=build_once(workspace,state,args.dry_run,eligible,eligible_repos,args.execution_grant,worker_identity)
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
