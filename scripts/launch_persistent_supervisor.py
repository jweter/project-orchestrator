#!/usr/bin/env python3
"""Fail-closed launcher/health handshake for the persistent Hands-Off supervisor."""
from __future__ import annotations
import argparse,json,os,subprocess,sys,time
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

def supervisor_environment(repo:Path)->dict[str,str]:
    """Expose the src-layout package to detached supervisor children."""
    env=os.environ.copy()
    src=str(repo/"src")
    existing=env.get("PYTHONPATH","")
    env["PYTHONPATH"]=src+(os.pathsep+existing if existing else "")
    return env

def interactive_human_control_root(state:Path)->Path|None:
    """Resolve the interactive user's control marker root without trusting SYSTEM's profile."""
    for candidate in (state,*state.parents):
        if candidate.parent.name.lower()=="users":
            return candidate/"AppData"/"Local"/"ExponentialEngine"/"flow-state"
    local_app_data=os.environ.get("LOCALAPPDATA")
    return Path(local_app_data)/"ExponentialEngine"/"flow-state" if local_app_data else None

def network_guardian_command(repo:Path,state:Path,human_control_root:Path|None=None)->list[str]:
    cmd=[sys.executable,str(repo/"scripts"/"network_self_heal.py"),
         "--state-root",str(state),"--poll-seconds","30","--failures","3","--allow-reboot"]
    if human_control_root is not None:
        cmd.extend(["--human-control-root",str(human_control_root)])
    return cmd

def network_guardian_authorized(yield_on_interactive_session:bool)->bool:
    """Only the elevated ONSTART owner is allowed to own network recovery."""
    return bool(yield_on_interactive_session)

def start_network_guardian(repo:Path,state:Path,human_control_root:Path|None=None)->bool:
    """Best-effort boot of the idempotent elevated Windows network guardian."""
    if os.name!="nt":
        return False
    flags=(getattr(subprocess,"CREATE_NEW_PROCESS_GROUP",0)
           |getattr(subprocess,"DETACHED_PROCESS",0)
           |getattr(subprocess,"CREATE_NO_WINDOW",0))
    try:
        subprocess.Popen(network_guardian_command(repo,state,human_control_root),cwd=repo,
                         stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                         creationflags=flags,close_fds=True)
    except OSError:
        return False
    return True

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

def supervisor_state_fresh(sup:dict,*,now:datetime|None=None,max_age_seconds:int=900)->bool:
    stamp=sup.get("heartbeat_at_utc") or sup.get("updated_at_utc")
    if not isinstance(stamp,str) or not stamp:
        return False
    try:
        updated=datetime.fromisoformat(stamp.replace("Z","+00:00"))
    except ValueError:
        return False
    current=now or datetime.now(UTC)
    if updated.tzinfo is None:
        updated=updated.replace(tzinfo=UTC)
    return 0 <= (current-updated).total_seconds() <= max_age_seconds

def publication_blocked_generation_live(state:Path,active_id:str,*,now:datetime|None=None,
                                        max_age_seconds:int=900)->bool:
    """Prove degraded liveness without promoting heartbeat evidence to authority."""
    proc=load(state/"supervisor-process-instance.json")
    heartbeat=load(state/"supervisor-generation-heartbeat.json")
    return bool(
        active_id
        and proc.get("process_instance_id")==active_id
        and proc.get("burst_id")
        and heartbeat.get("process_instance_id")==active_id
        and heartbeat.get("burst_id")==proc.get("burst_id")
        and heartbeat.get("status")=="STATE_PUBLICATION_BLOCKED"
        and supervisor_state_fresh(heartbeat,now=now,max_age_seconds=max_age_seconds)
    )

def publication_blocked_generation_stale(state:Path,active_id:str,*,now:datetime|None=None,
                                         max_age_seconds:int=900)->bool:
    """Identify the same publication-blocked generation after its heartbeat expires."""
    proc=load(state/"supervisor-process-instance.json")
    heartbeat=load(state/"supervisor-generation-heartbeat.json")
    return bool(
        active_id
        and proc.get("process_instance_id")==active_id
        and proc.get("burst_id")
        and heartbeat.get("process_instance_id")==active_id
        and heartbeat.get("burst_id")==proc.get("burst_id")
        and heartbeat.get("status")=="STATE_PUBLICATION_BLOCKED"
        and not supervisor_state_fresh(heartbeat,now=now,max_age_seconds=max_age_seconds)
    )

def request_stale_generation_handoff(state:Path,active_id:str,*,now:datetime|None=None)->dict:
    payload={"schema_version":1,"status":"TAKEOVER_REQUESTED",
             "predecessor_process_instance_id":active_id,
             "requested_at_utc":(now or datetime.now(UTC)).isoformat()}
    atomic(state/"supervisor-takeover-request.json",payload)
    return payload

def reclaim_stalled_generation(state:Path,active_id:str,*,now:datetime|None=None,
                               fingerprint_fn=None)->dict:
    """Fence only an exact generation proven stalled and still owning its PID."""
    from project_orchestrator.recovery_manager import process_start_fingerprint
    from project_orchestrator.supervisor_health import (
        evaluate_supervisor_health, fence_generation, verify_reclaim_identity,
    )
    proc=load(state/"supervisor-process-instance.json")
    heartbeat=load(state/"supervisor-generation-heartbeat.json")
    progress=load(state/"supervisor-progress.json")
    if proc.get("process_instance_id") != active_id:
        return {"status":"RECLAIM_IDENTITY_MISMATCH","reclaimed":False}
    health=evaluate_supervisor_health(process=proc,heartbeat=heartbeat,progress=progress,now=now)
    if health.get("status") != "SUPERVISOR_STALLED" or health.get("reclaimable") is not True:
        return {"status":health.get("status","RECLAIM_NOT_AUTHORIZED"),"reclaimed":False}
    verified=verify_reclaim_identity(health,fingerprint_fn=fingerprint_fn or process_start_fingerprint)
    if verified.get("verified") is not True:
        return {**verified,"reclaimed":False}
    fence=fence_generation(state,verified,now=now)
    return {**fence,"reclaimed":True}

def main()->int:
    p=argparse.ArgumentParser()
    p.add_argument("--repo-root",required=True);p.add_argument("--workspace-root",required=True)
    p.add_argument("--state-root",required=True);p.add_argument("--timeout-seconds",type=int,default=30)
    p.add_argument("--builder-seconds",type=int,default=300);p.add_argument("--merge-seconds",type=int,default=60)
    p.add_argument("--yield-on-interactive-session",action="store_true")
    p.add_argument("--wait-for-takeover",action="store_true",
                   help="keep retrying until this launcher becomes authoritative")
    p.add_argument("--stale-seconds",type=int,default=900,
                   help="request graceful generation handoff after canonical supervisor state is this old")
    a=p.parse_args();repo=Path(a.repo_root);state=Path(a.state_root);state.mkdir(parents=True,exist_ok=True)
    if network_guardian_authorized(a.yield_on_interactive_session):
        start_network_guardian(repo,state,interactive_human_control_root(state))
    before=load(state/"supervisor-process-instance.json").get("process_instance_id")
    log=state/"persistent-supervisor.log"
    cmd=supervisor_command(repo,a.workspace_root,state,a.builder_seconds,a.merge_seconds,
                           yield_on_interactive_session=a.yield_on_interactive_session)
    flags=getattr(subprocess,"CREATE_NEW_PROCESS_GROUP",0)|getattr(subprocess,"DETACHED_PROCESS",0)
    with log.open("ab") as out:
        child=subprocess.Popen(cmd,cwd=repo,stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,
                               creationflags=flags,close_fds=True,env=supervisor_environment(repo))
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
        heartbeat=load(state/"supervisor-generation-heartbeat.json")
        freshness=heartbeat if heartbeat.get("process_instance_id")==active_id else sup
        if (reentry.get("status")=="SUPERVISOR_INSTANCE_ALREADY_ACTIVE" and active_id
                and publication_blocked_generation_live(
                    state,active_id,max_age_seconds=max(1,a.stale_seconds))):
            receipt.update({"status":"WAITING_FOR_STATE_PUBLICATION","process_instance_id":active_id,
                            "burst_id":proc.get("burst_id"),
                            "observed_at_utc":datetime.now(UTC).isoformat()})
            atomic(state/"persistent-supervisor-launch.json",receipt)
            time.sleep(.25)
            continue
        if (reentry.get("status")=="SUPERVISOR_INSTANCE_ALREADY_ACTIVE" and active_id
                and publication_blocked_generation_stale(
                    state,active_id,max_age_seconds=max(1,a.stale_seconds))):
            request_stale_generation_handoff(state,active_id)
            reclaim=reclaim_stalled_generation(state,active_id)
            receipt.update({"status":"GENERATION_FENCED" if reclaim.get("reclaimed") else "STALE_GENERATION",
                            "process_instance_id":active_id,"burst_id":proc["burst_id"],
                            "reclaim_status":reclaim.get("status"),
                            "observed_at_utc":datetime.now(UTC).isoformat()})
            atomic(state/"persistent-supervisor-launch.json",receipt)
            if not a.wait_for_takeover:
                return 2
        if (reentry.get("status")=="SUPERVISOR_INSTANCE_ALREADY_ACTIVE" and active_id
                and proc.get("process_instance_id")==active_id
                and sup.get("process_instance_id")==active_id
                and proc.get("burst_id") and sup.get("burst_id")==proc.get("burst_id")
                and sup.get("updated_at_utc")):
            if not supervisor_state_fresh(freshness,max_age_seconds=max(1,a.stale_seconds)):
                request_stale_generation_handoff(state,active_id)
                reclaim=reclaim_stalled_generation(state,active_id)
                receipt.update({"status":"GENERATION_FENCED" if reclaim.get("reclaimed") else "STALE_GENERATION",
                                "process_instance_id":active_id,"burst_id":proc["burst_id"],
                                "reclaim_status":reclaim.get("status"),
                                "observed_at_utc":datetime.now(UTC).isoformat()})
                atomic(state/"persistent-supervisor-launch.json",receipt)
                if not a.wait_for_takeover:
                    return 2
            if not a.wait_for_takeover:
                receipt.update({"status":"EXISTING_ACTIVE","process_instance_id":active_id,
                                "burst_id":proc["burst_id"],
                                "verified_at_utc":datetime.now(UTC).isoformat()})
                atomic(state/"persistent-supervisor-launch.json",receipt);return 0
            # The interactive ONLOGON launcher must survive the race with the
            # SYSTEM boot owner. Wait for that child to yield, then spawn a new
            # current-user candidate instead of leaving supervision ownerless.
            if child.poll() is not None:
                time.sleep(.5)
                before=active_id
                with log.open("ab") as out:
                    child=subprocess.Popen(cmd,cwd=repo,stdin=subprocess.DEVNULL,
                        stdout=out,stderr=subprocess.STDOUT,creationflags=flags,close_fds=True,env=supervisor_environment(repo))
                deadline=time.monotonic()+max(1,a.timeout_seconds)
                receipt["launcher_pid"]=child.pid
                receipt["status"]="TAKEOVER_RETRY"
                atomic(state/"persistent-supervisor-launch.json",receipt)
        if child.poll() is not None:
            if a.wait_for_takeover:
                time.sleep(.5)
                before=load(state/"supervisor-process-instance.json").get("process_instance_id")
                with log.open("ab") as out:
                    child=subprocess.Popen(cmd,cwd=repo,stdin=subprocess.DEVNULL,
                        stdout=out,stderr=subprocess.STDOUT,creationflags=flags,close_fds=True,env=supervisor_environment(repo))
                deadline=time.monotonic()+max(1,a.timeout_seconds)
                receipt["launcher_pid"]=child.pid
                receipt["status"]="TAKEOVER_RETRY"
                atomic(state/"persistent-supervisor-launch.json",receipt)
                continue
            break
        time.sleep(.25)
    receipt.update({"status":"FAIL","reason":"NO_AUTHORITATIVE_SUPERVISOR_HEARTBEAT",
                    "observed_at_utc":datetime.now(UTC).isoformat(),"child_returncode":child.poll()})
    atomic(state/"persistent-supervisor-launch.json",receipt);return 2

if __name__=="__main__":
    raise SystemExit(main())
