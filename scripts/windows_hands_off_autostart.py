#!/usr/bin/env python3
"""Install or inspect the Windows cold-boot entrypoint for Hands-Off Evolution.

The scheduled task only starts the fail-closed launcher. The launcher must then
publish persistent-supervisor-launch.json PASS/EXISTING_ACTIVE before runtime is
considered healthy.
"""
from __future__ import annotations
import argparse,json,subprocess,sys
from pathlib import Path

TASK_NAME="ProjectHandsOffSupervisor"
BOOT_TASK_NAME="ProjectHandsOffSupervisorBoot"

def task_command(repo:Path,workspace:Path,state:Path)->str:
    launcher=repo/"scripts"/"launch_persistent_supervisor.py"
    return (f'"{sys.executable}" "{launcher}" --repo-root "{repo}" '
            f'--workspace-root "{workspace}" --state-root "{state}" '
            '--timeout-seconds 60 --builder-seconds 60 --merge-seconds 60')

def run(args:list[str])->subprocess.CompletedProcess[str]:
    return subprocess.run(args,text=True,capture_output=True,check=False)

def install(repo:Path,workspace:Path,state:Path)->int:
    if sys.platform!="win32":
        print(json.dumps({"status":"UNSUPPORTED_PLATFORM","platform":sys.platform}));return 2
    cmd=task_command(repo,workspace,state)
    # Two triggers cover both cold boot and interactive recovery. ONSTART is the
    # actual cold-boot authority; ONLOGON is a second idempotent wake. The
    # supervisor kernel lock and launcher handshake fence duplicate execution.
    created=[]
    for name,schedule in ((BOOT_TASK_NAME,"ONSTART"),(TASK_NAME,"ONLOGON")):
        args=["schtasks","/Create","/TN",name,"/SC",schedule,"/TR",cmd]
        if schedule=="ONSTART":
            args += ["/RU","SYSTEM","/RL","HIGHEST"]
        else:
            args += ["/RL","LIMITED"]
        result=run(args+["/F"])
        if result.returncode:
            print(json.dumps({"status":"INSTALL_FAILED","task":name,
                              "stderr":result.stderr.strip()}));return 2
        verify=run(["schtasks","/Query","/TN",name,"/FO","LIST","/V"])
        if verify.returncode or name not in verify.stdout:
            print(json.dumps({"status":"VERIFY_FAILED","task":name}));return 2
        created.append({"task":name,"schedule":schedule})
    print(json.dumps({"status":"INSTALLED","tasks":created,"command":cmd},indent=2))
    return 0

def inspect()->int:
    if sys.platform!="win32":
        print(json.dumps({"status":"UNSUPPORTED_PLATFORM","platform":sys.platform}));return 2
    tasks=[]
    ok=True
    for name in (BOOT_TASK_NAME,TASK_NAME):
        result=run(["schtasks","/Query","/TN",name,"/FO","LIST","/V"])
        present=result.returncode==0
        ok=ok and present
        tasks.append({"task":name,"status":"PRESENT" if present else "ABSENT",
                      "details":result.stdout.strip(),"stderr":result.stderr.strip()})
    print(json.dumps({"status":"PRESENT" if ok else "INCOMPLETE","tasks":tasks},indent=2))
    return 0 if ok else 1

def main()->int:
    p=argparse.ArgumentParser()
    p.add_argument("action",choices=["install","inspect"])
    p.add_argument("--repo-root");p.add_argument("--workspace-root");p.add_argument("--state-root")
    a=p.parse_args()
    if a.action=="inspect": return inspect()
    if not all([a.repo_root,a.workspace_root,a.state_root]):
        p.error("install requires --repo-root, --workspace-root, and --state-root")
    return install(Path(a.repo_root).resolve(),Path(a.workspace_root).resolve(),Path(a.state_root).resolve())

if __name__=="__main__": raise SystemExit(main())
