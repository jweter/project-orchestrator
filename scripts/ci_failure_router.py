"""Route failed GitHub CI into a deterministic, fail-closed repair packet."""
from __future__ import annotations
import argparse, hashlib, json, re, subprocess
from datetime import UTC, datetime
from pathlib import Path
from project_orchestrator.repair import fingerprint_failure
from project_orchestrator.failure_envelope import build_failure_envelope, first_meaningful_diagnostic

CLOSED_CLASSES={"TEST_FAILURE","SECURITY","DEPENDENCY","TYPE_CHECK","GOVERNANCE",
                "PROVENANCE","PRODUCT_REALITY","ENVIRONMENT","UNKNOWN"}
SAFE_CLASSES={"FORMAT","IMPORT_SORT","LINT_SAFE_FIX"}

def gh_json(args:list[str]):
    p=subprocess.run(["gh",*args],text=True,capture_output=True,check=False)
    if p.returncode:
        raise RuntimeError(p.stderr.strip() or "gh command failed")
    return json.loads(p.stdout or "null")

def first_meaningful_failure(log:str)->str:
    return first_meaningful_diagnostic(log)

def classify(log_or_line:str)->str:
    return fingerprint_failure(log_or_line).failure_class

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
    envelope=build_failure_envelope(
        repository=repo,
        exact_head_sha=head,
        stage=f"ci:{job['name']}",
        log_text=lp.stdout,
        environment_identity="github-actions",
    )
    failure=envelope["first_meaningful_diagnostic"]
    cls=envelope["failure_class"]
    # Classification is evidence only. Mutation authority is repository-local
    # and is checked by project_orchestrator.repair before any command executes.
    return {"schema_version":1,"status":"REPAIR_PACKET_READY","repository":repo,
            "pr_number":pr,"pr_url":data["url"],"head_sha":head,
            "workflow_run_id":run["databaseId"],"workflow_name":run["name"],
            "job_id":job["databaseId"],"job_name":job["name"],
            "first_meaningful_failure":failure,"classification":cls,
            "deterministic_auto_repair_candidate":cls in SAFE_CLASSES,
            "deterministic_auto_repair_eligible":False,
            "repair_authority_status":"REQUIRES_REPOSITORY_POLICY_CHECK",
            "fail_closed":True,
            "failure_fingerprint":envelope["failure_fingerprint"],
            "failure_envelope":envelope,
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
