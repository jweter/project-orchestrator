#!/usr/bin/env python3
"""Fail-closed launcher/health handshake for the persistent Hands-Off supervisor."""
from __future__ import annotations
import argparse,json,subprocess,sys,time
from datetime import UTC,datetime
from pathlib import Path

def load(path:Path):
    try:
        value=json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value,dict) else {}
    except (OSError,json.JSONDecodeError,TypeError,ValueError):
        return {}

def atomic(path:Path,payload:dict)->None:
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    tmp.replace(path)

def supervisor_command(repo:Path,workspace_root:str,state:Path,builder_seconds:int,merge_seconds:int,
                       *,yield_on_interactive_session:bool=False)->list[str]:
    """Build a package-aware supervisor command so ``scripts.*`` imports resolve."""
    cmd=[sys.executable,"-m","scripts.continuous_flow_supervisor",
         "--repo-root",str(repo),"--workspace-root",workspace_root,"--state-root",str(state),
         "--builder-seconds",str(builder_seconds),"--merge-seconds",str(merge_seconds)]
    if yield_on_interactive_session:
        cmd.append("--yield-on-interactive-session")
    return cmd

def verified_identity(state:Path,before_id:str|None)->dict|None:
    proc=load(state/"supervisor-process-instance.json")
    sup=load(state/"supervisor.json")
    instance=proc.get("process_instance_id")
    burst=proc.get("burst_id")
    if not instance or instance==before_id or not burst:
        return None
    if sup.get("process_instance_id")!=instance or sup.get("burst_id")!=burst:
        return None
    if not sup.get("updated_at_utc"):
        return None
    return {"process_instance_id":instance,"burst_id":burst,
            "supervisor_status":sup.get("status"),"builder_status":sup.get("builder_status")}

def main()->int:
    p=argparse.ArgumentParser()
    p.add_argument("--repo-root",required=True);p.add_argument("--workspace-root",required=True)
    p.add_argument("--state-root",required=True);p.add_argument("--timeout-seconds",type=int,default=30)
    p.add_argument("--builder-seconds",type=int,default=300);p.add_argument("--merge-seconds",type=int,default=60)
    p.add_argument("--yield-on-interactive-session",action="store_true")
    a=p.parse_args();repo=Path(a.repo_root);state=Path(a.state_root);state.mkdir(parents=True,exist_ok=True)
    before=load(state/"supervisor-process-instance.json").get("process_instance_id")
    log=state/"persistent-supervisor.log"
    cmd=supervisor_command(repo,a.workspace_root,state,a.builder_seconds,a.merge_seconds,
                           yield_on_interactive_session=a.yield_on_interactive_session)
    flags=getattr(subprocess,"CREATE_NEW_PROCESS_GROUP",0)|getattr(subprocess,"DETACHED_PROCESS",0)
    with log.open("ab") as out:
        child=subprocess.Popen(cmd,cwd=repo,stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,
                               creationflags=flags,close_fds=True)
    deadline=time.monotonic()+max(1,a.timeout_seconds)
    receipt={"schema_version":1,"requested_at_utc":datetime.now(UTC).isoformat(),
             "launcher_pid":child.pid,"previous_process_instance_id":before,"status":"PENDING"}
    atomic(state/"persistent-supervisor-launch.json",receipt)
    while time.monotonic()<deadline:
        identity=verified_identity(state,before)
        if identity:
            receipt.update(identity);receipt["status"]="PASS";receipt["verified_at_utc"]=datetime.now(UTC).isoformat()
            atomic(state/"persistent-supervisor-launch.json",receipt);return 0
        reentry=load(state/"supervisor-reentry.json")
        active_id=reentry.get("active_process_instance_id")
        proc=load(state/"supervisor-process-instance.json")
        sup=load(state/"supervisor.json")
        if (reentry.get("status")=="SUPERVISOR_INSTANCE_ALREADY_ACTIVE" and active_id
                and proc.get("process_instance_id")==active_id
                and sup.get("process_instance_id")==active_id
                and proc.get("burst_id") and sup.get("burst_id")==proc.get("burst_id")
                and sup.get("updated_at_utc")):
            receipt.update({"status":"EXISTING_ACTIVE","process_instance_id":active_id,
                            "burst_id":proc["burst_id"],
                            "verified_at_utc":datetime.now(UTC).isoformat()})
            atomic(state/"persistent-supervisor-launch.json",receipt);return 0
        if child.poll() is not None:
            break
        time.sleep(.25)
    receipt.update({"status":"FAIL","reason":"NO_AUTHORITATIVE_SUPERVISOR_HEARTBEAT",
                    "observed_at_utc":datetime.now(UTC).isoformat(),"child_returncode":child.poll()})
    atomic(state/"persistent-supervisor-launch.json",receipt);return 2

if __name__=="__main__":
    raise SystemExit(main())
