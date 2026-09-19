"""Idle-gated autonomous bounded-slice builder using local Ollama + Aider."""
from __future__ import annotations
import argparse, ctypes, json, os, shutil, subprocess, sys, time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
try:
    from scripts.lane_leases import open_pr_paths
except ModuleNotFoundError:
    from lane_leases import open_pr_paths

PORTFOLIO_REPOS=(
    "jweter/project-orchestrator","jweter/knowledge-engine-core",
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

def run(argv:list[str],cwd:Path,**kwargs:Any)->subprocess.CompletedProcess[str]:
    return subprocess.run(argv,cwd=cwd,text=True,capture_output=True,check=False,**kwargs)

def gh_json(args:list[str])->Any:
    p=subprocess.run(["gh",*args],text=True,capture_output=True,check=False)
    if p.returncode: raise RuntimeError(p.stderr.strip() or "gh failed")
    return json.loads(p.stdout)

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
    dirty=run(["git","status","--porcelain"],ws)
    if dirty.returncode or dirty.stdout.strip():
        raise RuntimeError("workspace dirty")
    fetch=run(["git","fetch","--prune","origin"],ws)
    if fetch.returncode: raise RuntimeError("fetch failed")
    return ws

def select_repo(state_path:Path)->str|None:
    prior=-1
    if state_path.exists():
        try: prior=int(json.loads(state_path.read_text(encoding="utf-8")).get("last_repo_index",-1))
        except Exception: prior=-1
    for offset in range(1,len(PORTFOLIO_REPOS)+1):
        idx=(prior+offset)%len(PORTFOLIO_REPOS)
        repo=PORTFOLIO_REPOS[idx]
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
def build_once(workspace_root:Path,state_path:Path,dry_run:bool=False)->dict[str,Any]:
    idle=windows_idle_seconds()
    if idle is not None and idle<MIN_IDLE_SECONDS:
        return {"status":"USER_ACTIVE","idle_seconds":idle}
    repo=select_repo(state_path)
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
    stamp=datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    branch=f"actor/autonomous-{repo.split('/')[-1].lower()}-{stamp}"
    if run(["git","checkout","-B",branch,base],ws).returncode:
        raise RuntimeError("branch creation failed")

    aider=run_aider(ws,prompt_for(repo))
    cleanup_aider_artifacts(ws)
    paths=changed_paths(ws,base)
    if aider.returncode!=0:
        reset_workspace(ws,base)
        result={"status":"BUILDER_FAILED","repository":repo,"returncode":aider.returncode,"last_repo_index":idx}
        atomic_json(state_path,result); return result
    if not paths:
        result={"status":"NO_OP","repository":repo,"last_repo_index":idx}
        atomic_json(state_path,result); return result
    blocked=protected_change(paths)
    if blocked:
        reset_workspace(ws,base)
        result={"status":"PROTECTED_PATH_BLOCKED","repository":repo,"path":blocked,"last_repo_index":idx}
        atomic_json(state_path,result); return result
    quality=semantic_quality_failure(ws,paths)
    if quality:
        reset_workspace(ws,base)
        result={"status":"SEMANTIC_QUALITY_BLOCKED","repository":repo,"reason":quality,"last_repo_index":idx}
        atomic_json(state_path,result); return result
    preflight=run_preflight(ws)
    if preflight.returncode!=0:
        reset_workspace(ws,base)
        result={"status":"PREFLIGHT_FAILED","repository":repo,"returncode":preflight.returncode,"last_repo_index":idx}
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
    body=("Autonomous laptop-actor bounded slice. Local Ollama/Aider produced the change; "
          "canonical exact-head preflight passed. Merge authority remains external and fail-closed.")
    created=subprocess.run(["gh","pr","create","--repo",repo,"--base",main,"--head",branch,
                            "--title","AUTO: bounded autonomous engineering slice","--body",body],
                           cwd=ws,text=True,capture_output=True,check=False)
    status="PR_OPEN" if created.returncode==0 else "PR_CREATE_FAILED"
    result={"status":status,"repository":repo,"branch":branch,"base_sha":base,
            "head_sha":head,"changed_paths":paths,"pr_url":created.stdout.strip(),
            "last_repo_index":idx}
    atomic_json(state_path,result)
    return result

def main()->int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--workspace-root",required=True)
    parser.add_argument("--state",required=True)
    parser.add_argument("--dry-run",action="store_true")
    args=parser.parse_args()
    state=Path(args.state)
    try:
        result=build_once(Path(args.workspace_root),state,args.dry_run)
    except (OSError,RuntimeError,subprocess.TimeoutExpired,json.JSONDecodeError) as exc:
        result={"status":"P1_BUILDER_FAILURE","error_class":type(exc).__name__}
        atomic_json(state,result)
    print(json.dumps(result,indent=2,sort_keys=True))
    return 0 if result["status"] not in {"P1_BUILDER_FAILURE","BUILDER_FAILED"} else 1

if __name__=="__main__":
    raise SystemExit(main())
