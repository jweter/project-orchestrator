"""Conservative always-on green-PR merger for the portfolio."""
from __future__ import annotations
import json, re, subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

PORTFOLIO_REPOS=(
    "jweter/project-orchestrator","jweter/knowledge-engine-core",
    "jweter/knowledge-engine-web","jweter/knowledge-engine-ai",
    "jweter/rocksmith-cdlc-generator","jweter/Project-Everward",
    "jweter/VoiceStudio",
)
PASS_CONCLUSIONS={"SUCCESS","NEUTRAL","SKIPPED"}
ALLOWED_AUTHORS={"jweter","dependabot[bot]"}
BLOCKING_RE=re.compile(r"(?i)(?:\bP[0-3]\b|blocking|must fix|security|incorrect|regression|data loss|usage limits?|review unavailable|unable to review|could not review)")
MIN_PR_AGE_SECONDS=120
REVIEW_SETTLE_SECONDS=300

@dataclass(frozen=True)
class MergeDecision:
    eligible: bool
    reason: str

def decide(pr:dict[str,Any], *, blocking_findings:bool=False, age_seconds:float=9999, review_settled:bool=True, allow_draft:bool=False)->MergeDecision:
    if pr.get("isDraft") and not allow_draft: return MergeDecision(False,"DRAFT")
    if pr.get("author",{}).get("login") not in ALLOWED_AUTHORS: return MergeDecision(False,"AUTHOR_NOT_ALLOWED")
    if age_seconds < MIN_PR_AGE_SECONDS: return MergeDecision(False,"PR_AGE_WINDOW")
    if not review_settled: return MergeDecision(False,"REVIEW_SETTLE_WINDOW")
    if pr.get("mergeStateStatus")!="CLEAN": return MergeDecision(False,f"MERGE_STATE_{pr.get('mergeStateStatus','UNKNOWN')}")
    if pr.get("reviewDecision") in {"CHANGES_REQUESTED","REVIEW_REQUIRED"}: return MergeDecision(False,f"REVIEW_{pr['reviewDecision']}")
    if blocking_findings: return MergeDecision(False,"BLOCKING_REVIEW_FINDING")
    checks=pr.get("statusCheckRollup")
    if not isinstance(checks,list) or not checks: return MergeDecision(False,"CHECKS_MISSING")
    for check in checks:
        status=str(check.get("status","")).upper()
        conclusion=str(check.get("conclusion","")).upper()
        if status!="COMPLETED": return MergeDecision(False,"CHECKS_PENDING")
        if conclusion not in PASS_CONCLUSIONS: return MergeDecision(False,f"CHECK_{conclusion or 'UNKNOWN'}")
    return MergeDecision(True,"GREEN")

def gh_json(args:list[str])->Any:
    p=subprocess.run(["gh",*args],text=True,encoding="utf-8",errors="replace",capture_output=True,check=False)
    if p.returncode: raise RuntimeError(p.stderr.strip() or "gh failed")
    return json.loads(p.stdout)

def gh_paginated(endpoint:str)->list[dict[str,Any]]:
    p=subprocess.run(["gh","api","--paginate","--slurp",endpoint],text=True,encoding="utf-8",errors="replace",capture_output=True,check=False)
    if p.returncode: raise RuntimeError(p.stderr.strip() or "gh api pagination failed")
    pages=json.loads(p.stdout)
    if not isinstance(pages,list): raise RuntimeError("invalid paginated GitHub response")
    return [item for page in pages if isinstance(page,list) for item in page if isinstance(item,dict)]

def open_prs(repo:str)->list[dict[str,Any]]:
    fields="number,isDraft,headRefOid,mergeStateStatus,reviewDecision,statusCheckRollup,createdAt,author"
    return gh_json(["pr","list","--repo",repo,"--state","open","--json",fields,"--limit","100"])

def _age_seconds(created_at:str)->float:
    created=datetime.fromisoformat(created_at.replace("Z","+00:00"))
    return max(0.0,(datetime.now(UTC)-created).total_seconds())

def review_settled(repo:str, number:int, head_sha:str, created_at:str)->bool:
    """Require a quiet review window anchored to the current exact head."""
    created=datetime.fromisoformat(created_at.replace("Z","+00:00"))
    commit=gh_json(["api",f"repos/{repo}/commits/{head_sha}"])
    committed_at=((commit.get("commit") or {}).get("committer") or {}).get("date")
    if not committed_at:
        return False
    head_time=datetime.fromisoformat(str(committed_at).replace("Z","+00:00"))
    latest=max(created,head_time)
    comments=gh_paginated(f"repos/{repo}/pulls/{number}/comments")
    for comment in comments:
        if str(comment.get("commit_id") or "") != head_sha:
            continue
        stamp=comment.get("updated_at") or comment.get("created_at")
        if stamp:
            latest=max(latest,datetime.fromisoformat(str(stamp).replace("Z","+00:00")))
    reviews=gh_paginated(f"repos/{repo}/pulls/{number}/reviews")
    for review in reviews:
        commit=str(review.get("commit_id") or "")
        if commit and commit != head_sha:
            continue
        stamp=review.get("submitted_at")
        if stamp:
            latest=max(latest,datetime.fromisoformat(str(stamp).replace("Z","+00:00")))
    return (datetime.now(UTC)-latest).total_seconds() >= REVIEW_SETTLE_SECONDS

def blocking_review_findings(repo:str, number:int, head_sha:str)->bool:
    owner,name=repo.split("/",1)
    query="""query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){pullRequest(number:$number){reviewThreads(first:100){nodes{isResolved comments(first:100){nodes{body commit{oid}}}}}}}}"""
    payload=gh_json(["api","graphql","-f",f"query={query}","-F",f"owner={owner}","-F",f"name={name}","-F",f"number={number}"])
    threads=((((payload.get("data") or {}).get("repository") or {}).get("pullRequest") or {}).get("reviewThreads") or {}).get("nodes")
    if not isinstance(threads,list):
        raise RuntimeError("review thread resolution state unavailable")
    for thread in threads:
        if thread.get("isResolved") is True:
            continue
        comments=((thread.get("comments") or {}).get("nodes") or [])
        for comment in comments:
            body=str(comment.get("body",""))
            commit=str(((comment.get("commit") or {}).get("oid")) or "")
            if commit == head_sha and BLOCKING_RE.search(body):
                return True
    return False
def promote(repo:str,pr:dict[str,Any],dry_run:bool=False)->dict[str,Any]:
    head=str(pr.get("headRefOid") or "")
    findings=blocking_review_findings(repo,int(pr["number"]),head)
    decision=decide(pr,blocking_findings=findings,age_seconds=_age_seconds(str(pr["createdAt"])),review_settled=review_settled(repo,int(pr["number"]),head,str(pr["createdAt"])),allow_draft=True)
    result={"repository":repo,"pr":pr["number"],"head":head,"decision":decision.reason,"promoted":False}
    if not pr.get("isDraft"):
        result["decision"]="ALREADY_READY"
        return result
    if not decision.eligible or dry_run:return result
    proc=subprocess.run(["gh","pr","ready",str(pr["number"]),"--repo",repo],text=True,encoding="utf-8",errors="replace",capture_output=True,check=False)
    result["promoted"]=proc.returncode==0
    result["promotion_result"]="SUCCESS" if proc.returncode==0 else "FAILED"
    if proc.returncode: result["error_class"]="GH_PROMOTION_FAILED"
    return result

def merge(repo:str,pr:dict[str,Any],dry_run:bool=False)->dict[str,Any]:
    head=str(pr.get("headRefOid") or "")
    findings=blocking_review_findings(repo,int(pr["number"]),head)
    decision=decide(pr,blocking_findings=findings,age_seconds=_age_seconds(str(pr["createdAt"])),review_settled=review_settled(repo,int(pr["number"]),head,str(pr["createdAt"])))
    result={"repository":repo,"pr":pr["number"],"head":head,"decision":decision.reason,"merged":False}
    if not decision.eligible or dry_run:return result
    cmd=["gh","pr","merge",str(pr["number"]),"--repo",repo,"--squash","--delete-branch","--match-head-commit",head]
    proc=subprocess.run(cmd,text=True,encoding="utf-8",errors="replace",capture_output=True,check=False)
    result["merged"]=proc.returncode==0
    result["merge_result"]="SUCCESS" if proc.returncode==0 else "FAILED"
    if proc.returncode:
        result["error_class"]="GH_MERGE_FAILED"
    return result

def cycle(dry_run:bool=False)->list[dict[str,Any]]:
    results=[]
    for repo in PORTFOLIO_REPOS:
        for pr in open_prs(repo):
            if pr.get("isDraft"):
                promotion=promote(repo,pr,dry_run)
                results.append(promotion)
                # Promotion changes GitHub state. Re-fetch the exact PR before any
                # merge decision so a stale draft snapshot can never authorize merge.
                if promotion.get("promoted") and not dry_run:
                    refreshed=gh_json(["pr","view",str(pr["number"]),"--repo",repo,"--json",
                        "number,isDraft,headRefOid,mergeStateStatus,reviewDecision,statusCheckRollup,createdAt,author"])
                    results.append(merge(repo,refreshed,dry_run))
            else:
                results.append(merge(repo,pr,dry_run))
    return results

if __name__=="__main__":
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("--dry-run",action="store_true")
    args=parser.parse_args()
    print(json.dumps(cycle(args.dry_run),indent=2))
