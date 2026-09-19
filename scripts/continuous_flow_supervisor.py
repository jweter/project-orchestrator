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

def run_builder(repo_root:Path,workspace_root:Path,state_root:Path)->int:
    p=subprocess.run([sys.executable,str(repo_root/"scripts"/"autonomous_slice_builder.py"),
        "--workspace-root",str(workspace_root),"--state",str(state_root/"builder.json")],
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
        rc=run_builder(repo,workspace,state)
        healthy=rc==0 and linear_rc in (None,0)
        atomic(state/"supervisor.json",{"updated_at_utc":datetime.now(UTC).isoformat(),
            "status":"HEALTHY" if healthy else "FLOW_DEGRADED","builder_returncode":rc,
            "linear_reconciliation_returncode":linear_rc,
            "merge_watcher_pid":merge.pid,"merge_watcher_running":merge.poll() is None})
        if a.once:
            merge.terminate(); return 0 if rc==0 else 1
        time.sleep(max(60,a.builder_seconds))
if __name__=="__main__": raise SystemExit(main())
