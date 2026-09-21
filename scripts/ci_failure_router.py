"""Route failed GitHub CI into a deterministic, fail-closed repair packet."""
from __future__ import annotations
import argparse, hashlib, json, re, subprocess
from datetime import UTC, datetime
from pathlib import Path

CLOSED_CLASSES={"TEST_FAILURE","SECURITY","DEPENDENCY","TYPE_CHECK","GOVERNANCE",
                "PROVENANCE","PRODUCT_REALITY","ENVIRONMENT","UNKNOWN"}
SAFE_CLASSES={"FORMAT","IMPORT_SORT","LINT_SAFE_FIX"}

def gh_json(args:list[str]):
    p=subprocess.run(["gh",*args],text=True,capture_output=True,check=False)
    if p.returncode:
        raise RuntimeError(p.stderr.strip() or "gh command failed")
    return json.loads(p.stdout or "null")

def first_meaningful_failure(log:str)->str:
    noise=("##[","post job cleanup","process completed with exit code")
    for raw in log.splitlines():
        line=re.sub(r"^\d{4}-\d\d-\d\dT[^ ]+Z\s*","",raw).strip()
        low=line.lower()
        if not line or any(n in low for n in noise): continue
        if ("error" in low or "failed" in low or "failure" in low or
            "syntaxerror" in low or "traceback" in low):
            return line[:1200]
    return "UNKNOWN: no meaningful failure line found"
def classify(line:str)->str:
    s=line.lower()
    if "syntaxerror" in s or "pytest" in s or "assert" in s: return "TEST_FAILURE"
    if "ruff format" in s or "black" in s or "formatting" in s: return "FORMAT"
    if "isort" in s or "import sort" in s: return "IMPORT_SORT"
    if "ruff" in s or "flake8" in s or "lint" in s: return "LINT_SAFE_FIX"
    if "mypy" in s or "type check" in s: return "TYPE_CHECK"
    if "dependenc" in s or "resolutionimpossible" in s: return "DEPENDENCY"
    if "security" in s or "secret" in s: return "SECURITY"
    if "provenance" in s or "license" in s: return "PROVENANCE"
    if "environment" in s or "runner" in s or "no space left" in s: return "ENVIRONMENT"
    return "UNKNOWN"

def fingerprint(repo:str,head:str,job:str,classification:str,failure:str)->str:
    stable=re.sub(r"\d+","<n>",failure.lower())
    return hashlib.sha256(f"{repo}|{job}|{classification}|{stable}".encode()).hexdigest()[:20]

def build_packet(repo:str,pr:int)->dict:
    data=gh_json(["pr","view",str(pr),"--repo",repo,"--json","headRefOid,url,title"])
    head=data["headRefOid"]
    runs=gh_json(["run","list","--repo",repo,"--commit",head,"--event","pull_request",
                  "--json","databaseId,status,conclusion,name","--limit","20"])
    failed=[r for r in runs if r.get("status")=="completed" and r.get("conclusion")=="failure"]
    if not failed: raise RuntimeError("no failed pull-request workflow for exact head")
    run=failed[0]
    detail=gh_json(["run","view",str(run["databaseId"]),"--repo",repo,"--json","jobs"])
    jobs=[j for j in detail.get("jobs",[]) if j.get("conclusion")=="failure"]
    if not jobs: raise RuntimeError("failed workflow has no failed job")
    job=jobs[0]
    lp=subprocess.run(["gh","run","view",str(run["databaseId"]),"--repo",repo,
                       "--job",str(job["databaseId"]),"--log"],text=True,capture_output=True,check=False)
    if lp.returncode: raise RuntimeError(lp.stderr.strip() or "job log unavailable")
    failure=first_meaningful_failure(lp.stdout)
    cls=classify(failure)
    return {"schema_version":1,"status":"REPAIR_PACKET_READY","repository":repo,
            "pr_number":pr,"pr_url":data["url"],"head_sha":head,
            "workflow_run_id":run["databaseId"],"workflow_name":run["name"],
            "job_id":job["databaseId"],"job_name":job["name"],
            "first_meaningful_failure":failure,"classification":cls,
            "deterministic_auto_repair_eligible":cls in SAFE_CLASSES,
            "fail_closed":cls in CLOSED_CLASSES,
            "failure_fingerprint":fingerprint(repo,head,job["name"],cls,failure),
            "observed_at_utc":datetime.now(UTC).isoformat()}

def main()->int:
    p=argparse.ArgumentParser(); p.add_argument("--repo",required=True)
    p.add_argument("--pr",required=True,type=int); p.add_argument("--output")
    a=p.parse_args()
    try: packet=build_packet(a.repo,a.pr)
    except Exception as exc:
        packet={"schema_version":1,"status":"ROUTING_FAILED","fail_closed":True,
                "reason":str(exc),"observed_at_utc":datetime.now(UTC).isoformat()}
    text=json.dumps(packet,indent=2,sort_keys=True)+"\n"
    if a.output:
        output=Path(a.output); output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(text,encoding="utf-8")
    print(text,end=""); return 0 if packet["status"]=="REPAIR_PACKET_READY" else 2
if __name__=="__main__": raise SystemExit(main())
