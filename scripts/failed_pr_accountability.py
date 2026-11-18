"""Detect failed exact-head PRs and route them into repair packets."""
from __future__ import annotations
import json, subprocess
from datetime import UTC, datetime
from pathlib import Path
from scripts.green_pr_merger import PORTFOLIO_REPOS, open_prs
from scripts import pr_review_intake

FAIL_CONCLUSIONS={"FAILURE","CANCELLED","TIMED_OUT","ACTION_REQUIRED","STARTUP_FAILURE","STALE"}

def failed(pr:dict)->bool:
    checks=pr.get("statusCheckRollup")
    return isinstance(checks,list) and any(str(c.get("status","")).upper()=="COMPLETED" and str(c.get("conclusion","")).upper() in FAIL_CONCLUSIONS for c in checks)

def route(repo_root:Path,repo:str,number:int,state_root:Path)->dict:
    output=state_root/("failed-pr-"+repo.replace("/","-")+"-"+str(number)+".json")
    proc=subprocess.run(["python",str(repo_root/"scripts"/"ci_failure_router.py"),"--repo",repo,"--pr",str(number),"--output",str(output)],cwd=repo_root,text=True,capture_output=True,check=False)
    try:
        return json.loads(output.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError):
        return {"status":"ROUTING_FAILED","reason":(proc.stderr or proc.stdout)[-1000:]}

def cycle(repo_root:Path,state_root:Path)->list[dict]:
    results=[]
    for repo in PORTFOLIO_REPOS:
        try:
            prs=open_prs(repo)
        except Exception as exc:
            results.append({"repository":repo,"state":"OBSERVATION_FAILED","error_class":type(exc).__name__})
            continue
        for pr in prs:
            # Reuse the established PR controller and its owner-veto/lock.
            # Review findings become inert exact-head evidence, never a new
            # worker or a source of execution authority. CI routing is separate.
            review = pr_review_intake.observe(repo, pr, state_root)
            if review is not None and review.get("state") != "REVIEW_INTAKE_UNCHANGED":
                results.append(review)
            if not failed(pr):
                continue
            number=int(pr["number"]); head=str(pr.get("headRefOid") or "")
            packet=route(repo_root,repo,number,state_root)
            results.append({"repository":repo,"pr":number,"head":head,"state":"REPAIR_QUEUED" if packet.get("status")=="REPAIR_PACKET_READY" else "REPAIR_ROUTING_FAILED","failure_fingerprint":packet.get("failure_fingerprint"),"observed_at_utc":datetime.now(UTC).isoformat()})
    return results
