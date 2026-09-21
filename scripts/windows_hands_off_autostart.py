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
    cmd=str(repo/"scripts"/"windows_hands_off_boot.bat")
    # ONLOGON avoids requiring a stored account password while still recovering
    # automatically after a real reboot when the engineering account signs in.
    result=run(["schtasks","/Create","/TN",TASK_NAME,"/SC","ONLOGON","/TR",cmd,
                "/RL","LIMITED","/F"])
    if result.returncode:
        print(json.dumps({"status":"INSTALL_FAILED","stderr":result.stderr.strip()}));return 2
    verify=run(["schtasks","/Query","/TN",TASK_NAME,"/FO","LIST","/V"])
    status="INSTALLED" if verify.returncode==0 and TASK_NAME in verify.stdout else "VERIFY_FAILED"
    print(json.dumps({"status":status,"task":TASK_NAME,"command":cmd,
                      "query_returncode":verify.returncode},indent=2))
    return 0 if status=="INSTALLED" else 2

def inspect()->int:
    if sys.platform!="win32":
        print(json.dumps({"status":"UNSUPPORTED_PLATFORM","platform":sys.platform}));return 2
    result=run(["schtasks","/Query","/TN",TASK_NAME,"/FO","LIST","/V"])
    print(json.dumps({"status":"PRESENT" if result.returncode==0 else "ABSENT",
                      "task":TASK_NAME,"details":result.stdout.strip(),
                      "stderr":result.stderr.strip()},indent=2))
    return 0 if result.returncode==0 else 1

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
