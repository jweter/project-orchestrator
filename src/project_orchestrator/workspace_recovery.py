from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .grant_lifecycle import acquire_lifecycle_lock, atomic_lifecycle_json, release_lifecycle_lock
from .recovery_manager import (
    RECOVERY_GENERATION_LEASE_SECONDS,
    process_start_fingerprint,
    worker_liveness,
)
from .state_io import atomic_json


def repository_preflight_command(ws:Path,evidence_path:Path|None=None)->list[str]|None:
    """Resolve the repository's canonical local preflight without inventing a verifier."""
    engineering=Path("engineering/preflight.py")
    if (ws/engineering).is_file():
        command=[sys.executable,str(engineering)]
        if evidence_path is not None:
            command.extend(["--evidence",str(evidence_path)])
        return command
    legacy=Path("scripts/preflight.py")
    if (ws/legacy).is_file():
        return [sys.executable,str(legacy)]
    return None


def run_repository_preflight(ws:Path)->subprocess.CompletedProcess[str]|None:
    """Run repository-native validation while redirecting supported evidence outside preserved work."""
    with tempfile.TemporaryDirectory(prefix="azathoth-preserved-preflight-") as temp_dir:
        evidence_path=Path(temp_dir)/"preflight-evidence.json"
        command=repository_preflight_command(ws,evidence_path)
        if command is None:
            return None
        return subprocess.run(command,cwd=ws,text=True,capture_output=True,check=False)


def classify_orphan_branch(repo:str,ws:Path,branch:str)->tuple[str,dict]:
    """Classify clean autonomous branch state without mutating it."""
    base=subprocess.run(["git","merge-base","HEAD","origin/main"],cwd=ws,text=True,capture_output=True,check=False)
    head=subprocess.run(["git","rev-parse","HEAD"],cwd=ws,text=True,capture_output=True,check=False)
    if base.returncode or head.returncode:
        return "ORPHAN_HISTORY_UNREADABLE",{}
    base_sha=base.stdout.strip(); head_sha=head.stdout.strip()
    if not base_sha or not head_sha:
        return "ORPHAN_HISTORY_UNREADABLE",{}
    if head_sha==base_sha:
        return "ORPHAN_NO_DIFF",{"base_sha":base_sha,"head_sha":head_sha}
    remote=subprocess.run(["git","ls-remote","--heads","origin",branch],cwd=ws,text=True,capture_output=True,check=False)
    if remote.returncode:
        return "ORPHAN_REMOTE_UNREADABLE",{"base_sha":base_sha,"head_sha":head_sha}
    remote_head=remote.stdout.split()[0] if remote.stdout.strip() else ""
    pr=subprocess.run(["gh","pr","list","--repo",repo,"--head",branch,"--state","open","--json","number,url,headRefOid"],
                      cwd=ws,text=True,capture_output=True,check=False)
    if pr.returncode:
        return "ORPHAN_PR_STATE_UNREADABLE",{"base_sha":base_sha,"head_sha":head_sha}
    try:
        prs=json.loads(pr.stdout)
    except json.JSONDecodeError:
        return "ORPHAN_PR_STATE_UNREADABLE",{"base_sha":base_sha,"head_sha":head_sha}
    evidence={"base_sha":base_sha,"head_sha":head_sha,"remote_head_sha":remote_head}
    if prs:
        evidence["pr_number"]=prs[0].get("number"); evidence["pr_url"]=prs[0].get("url")
        if prs[0].get("headRefOid")==head_sha:
            return "ORPHAN_PR_RECOVERABLE",evidence
        return "ORPHAN_PR_HEAD_MISMATCH",evidence
    if remote_head==head_sha:
        return "ORPHAN_PUSHED_NO_PR",evidence
    if not remote_head:
        return "ORPHAN_LOCAL_COMMIT_UNPUSHED",evidence
    return "ORPHAN_REMOTE_HEAD_MISMATCH",evidence

def resume_orphan_branch(repo:str,ws:Path,branch:str,classification:str,evidence:dict)->str:
    """Resume only exact-head committed orphan work; never repair or rewrite history here."""
    expected=evidence.get("head_sha","")
    actual=subprocess.run(["git","rev-parse","HEAD"],cwd=ws,text=True,capture_output=True,check=False)
    if actual.returncode or actual.stdout.strip()!=expected:
        return "RECOVERY_HEAD_CHANGED"
    if classification=="ORPHAN_LOCAL_COMMIT_UNPUSHED":
        preflight=run_repository_preflight(ws)
        if preflight is None:
            return "RECOVERY_PREFLIGHT_UNAVAILABLE"
        if preflight.returncode:
            return "RECOVERY_PREFLIGHT_FAILED"
        pushed=subprocess.run(["git","push","-u","origin",branch],cwd=ws,text=True,capture_output=True,check=False)
        if pushed.returncode:
            return "RECOVERY_PUSH_FAILED"
        remote=subprocess.run(["git","ls-remote","--heads","origin",branch],cwd=ws,text=True,capture_output=True,check=False)
        if remote.returncode or not remote.stdout.strip() or remote.stdout.split()[0]!=expected:
            return "RECOVERY_REMOTE_HEAD_MISMATCH"
        classification="ORPHAN_PUSHED_NO_PR"
    if classification=="ORPHAN_PUSHED_NO_PR":
        remote_expected=evidence.get("remote_head_sha") or expected
        remote=subprocess.run(["git","ls-remote","--heads","origin",branch],cwd=ws,text=True,capture_output=True,check=False)
        if remote.returncode or not remote.stdout.strip() or remote.stdout.split()[0]!=remote_expected or remote_expected!=expected:
            return "RECOVERY_REMOTE_HEAD_MISMATCH"
        created=subprocess.run(["gh","pr","create","--repo",repo,"--base","main","--head",branch,
                                "--title","AUTO: recovered autonomous engineering slice",
                                "--body","Recovered exact-head autonomous work after worker lease expiry. Local preflight and remote SHA were verified before PR creation."],
                               cwd=ws,text=True,capture_output=True,check=False)
        return "RECOVERY_PR_OPENED" if created.returncode==0 else "RECOVERY_PR_CREATE_FAILED"
    return classification

def builder_artifacts_only(evidence:dict)->bool:
    """True only for captured zero-diff Aider bookkeeping, never product work."""
    prefixes=(".aider.chat.history.md",".aider.input.history",".aider.tags.cache.v4/")
    untracked=evidence.get("untracked",[])
    return (evidence.get("capture_status")=="CAPTURED"
        and evidence.get("diff_bytes")==0 and not evidence.get("changed_paths")
        and bool(untracked)
        and all(any(item.get("path","")==prefix or item.get("path","").startswith(prefix)
                    for prefix in prefixes) for item in untracked))

def preserved_work_evidence(ws:Path,base_sha:str)->dict:
    """Capture bounded, non-destructive identity for uncertain local work."""
    status=subprocess.run(["git","status","--porcelain"],cwd=ws,text=True,capture_output=True,check=False)
    head=subprocess.run(["git","rev-parse","HEAD"],cwd=ws,text=True,capture_output=True,check=False)
    branch=subprocess.run(["git","branch","--show-current"],cwd=ws,text=True,capture_output=True,check=False)
    names=subprocess.run(["git","diff","--name-only",base_sha],cwd=ws,text=True,capture_output=True,check=False)
    diff=subprocess.run(["git","diff","--binary",base_sha],cwd=ws,text=True,capture_output=True,check=False)
    if any(x.returncode for x in (status,head,branch,names,diff)):
        return {"capture_status":"UNREADABLE"}
    import hashlib
    payload=diff.stdout.encode("utf-8",errors="replace")
    untracked=subprocess.run(["git","ls-files","--others","--exclude-standard","-z"],cwd=ws,text=True,capture_output=True,check=False)
    if untracked.returncode:
        return {"capture_status":"UNREADABLE"}
    untracked_records=[]
    for rel in untracked.stdout.split(chr(0)):
        if not rel:
            continue
        path=ws/rel
        try:
            if path.is_symlink():
                target=os.readlink(path)
                data=target.encode("utf-8",errors="surrogateescape")
                kind="symlink"
            elif path.is_file():
                size=path.stat().st_size
                if size>10*1024*1024:
                    return {"capture_status":"UNTRACKED_FILE_TOO_LARGE","path":rel}
                data=path.read_bytes()
                kind="file"
            else:
                return {"capture_status":"UNTRACKED_SPECIAL_FILE","path":rel}
        except OSError:
            return {"capture_status":"UNTRACKED_FILE_UNREADABLE","path":rel}
        untracked_records.append({"path":rel,"kind":kind,"bytes":len(data),
                                  "sha256":hashlib.sha256(data).hexdigest()})
    return {
        "capture_status":"CAPTURED",
        "branch":branch.stdout.strip(),
        "head_sha":head.stdout.strip(),
        "status_porcelain":status.stdout[:12000],
        "changed_paths":[p for p in names.stdout.splitlines() if p][:500],
        "diff_sha256":hashlib.sha256(payload).hexdigest(),
        "diff_bytes":len(payload),
        "untracked":untracked_records,
        "repair_eligible":not bool(untracked_records),
    }

def revalidate_preserved_work(ws:Path,record:dict,evidence_fn=None)->str:
    """Prove quarantined local work is byte-identical before any future repair."""
    evidence_fn = evidence_fn or preserved_work_evidence
    expected=record.get("preserved_work_evidence")
    base=record.get("base_sha")
    if not isinstance(expected,dict) or expected.get("capture_status")!="CAPTURED" or not base:
        return "PRESERVED_EVIDENCE_MISSING"
    current=evidence_fn(ws,base)
    if current.get("capture_status")!="CAPTURED":
        return "PRESERVED_WORK_UNREADABLE"
    identity=("branch","head_sha","status_porcelain","changed_paths","diff_sha256","diff_bytes","untracked","repair_eligible")
    if any(current.get(k)!=expected.get(k) for k in identity):
        return "PRESERVED_WORK_CHANGED"
    return "PRESERVED_WORK_EXACT"

def validate_preserved_work(ws:Path,record:dict,revalidate_fn=None)->str:
    """Run deterministic validation only between two exact identity proofs."""
    revalidate_fn = revalidate_fn or revalidate_preserved_work
    if revalidate_fn(ws,record)!="PRESERVED_WORK_EXACT":
        return "PRESERVED_VALIDATION_IDENTITY_PRECHECK_FAILED"
    before=record.get("preserved_work_evidence",{}).get("diff_sha256")
    preflight=run_repository_preflight(ws)
    if preflight is None:
        return "PRESERVED_VALIDATION_PREFLIGHT_UNAVAILABLE"
    if revalidate_fn(ws,record)!="PRESERVED_WORK_EXACT":
        return "PRESERVED_VALIDATION_MUTATED_WORKSPACE"
    if preflight.returncode:
        return "PRESERVED_VALIDATION_FAILED"
    if not before:
        return "PRESERVED_VALIDATION_EVIDENCE_INCOMPLETE"
    return "PRESERVED_VALIDATION_PASSED"

def issue_preserved_recovery_capability(record:dict)->dict|None:
    """Issue inert, single-use authority bound to exact validated preserved work."""
    evidence=record.get("preserved_work_evidence",{})
    if record.get("preserved_work_validation")!="PRESERVED_VALIDATION_PASSED":
        return None
    if evidence.get("repair_eligible") is not True:
        return None
    required=("repository","base_sha","branch")
    if any(not record.get(k) for k in required) or not evidence.get("diff_sha256"):
        return None
    import secrets
    return {
        "capability_id":secrets.token_urlsafe(18),
        "state":"ISSUED",
        "repository":record["repository"],
        "base_sha":record["base_sha"],
        "branch":record["branch"],
        "preserved_diff_sha256":evidence["diff_sha256"],
        "preserved_head_sha":evidence.get("head_sha"),
        "issued_at":datetime.now(UTC).isoformat(),
    }

def consume_preserved_recovery_capability(ws:Path,record:dict,revalidate_fn=None)->str:
    """Consume exact recovery authority once; does not itself mutate product work."""
    revalidate_fn = revalidate_fn or revalidate_preserved_work
    cap=record.get("preserved_recovery_capability")
    if not isinstance(cap,dict) or cap.get("state")!="ISSUED":
        return "RECOVERY_CAPABILITY_UNAVAILABLE"
    if record.get("preserved_work_validation")!="PRESERVED_VALIDATION_PASSED":
        return "RECOVERY_CAPABILITY_VALIDATION_STALE"
    evidence=record.get("preserved_work_evidence",{})
    if evidence.get("repair_eligible") is not True:
        return "RECOVERY_CAPABILITY_REPAIR_NOT_AUTHORIZED"
    bindings={
        "repository":record.get("repository"),"base_sha":record.get("base_sha"),
        "branch":record.get("branch"),"preserved_diff_sha256":evidence.get("diff_sha256"),
        "preserved_head_sha":evidence.get("head_sha"),
    }
    if any(cap.get(k)!=v for k,v in bindings.items()):
        return "RECOVERY_CAPABILITY_BINDING_MISMATCH"
    if revalidate_fn(ws,record)!="PRESERVED_WORK_EXACT":
        return "RECOVERY_CAPABILITY_IDENTITY_CHANGED"
    cap["state"]="CONSUMED"
    cap["consumed_at"]=datetime.now(UTC).isoformat()
    return "RECOVERY_CAPABILITY_CONSUMED"

def commit_preserved_work(ws:Path,record:dict,consume_fn=None)->str:
    """Consume exact recovery authority and durably preserve validated work in Git."""
    consume_fn = consume_fn or consume_preserved_recovery_capability
    outcome=consume_fn(ws,record)
    if outcome!="RECOVERY_CAPABILITY_CONSUMED":
        return outcome
    cap=record["preserved_recovery_capability"]
    cap["outcome"]="MUTATION_STARTED"
    expected=record.get("preserved_work_evidence",{})
    add=subprocess.run(["git","add","-A"],cwd=ws,text=True,capture_output=True,check=False)
    if add.returncode:
        cap["outcome"]="STAGE_FAILED"
        return "PRESERVED_COMMIT_STAGE_FAILED"
    commit=subprocess.run(["git","commit","-m","chore: recover validated autonomous work"],cwd=ws,text=True,capture_output=True,check=False)
    if commit.returncode:
        cap["outcome"]="COMMIT_FAILED"
        return "PRESERVED_COMMIT_FAILED"
    head=subprocess.run(["git","rev-parse","HEAD"],cwd=ws,text=True,capture_output=True,check=False)
    branch=subprocess.run(["git","branch","--show-current"],cwd=ws,text=True,capture_output=True,check=False)
    if head.returncode or branch.returncode or branch.stdout.strip()!=record.get("branch"):
        cap["outcome"]="IDENTITY_FAILED"
        return "PRESERVED_COMMIT_IDENTITY_FAILED"
    new_head=head.stdout.strip()
    changed=subprocess.run(["git","diff","--binary",record["base_sha"],new_head],cwd=ws,text=True,capture_output=True,check=False)
    import hashlib
    if changed.returncode or hashlib.sha256(changed.stdout.encode("utf-8",errors="replace")).hexdigest()!=expected.get("diff_sha256"):
        cap["outcome"]="FINGERPRINT_MISMATCH"
        return "PRESERVED_COMMIT_FINGERPRINT_MISMATCH"
    record["execution_stage"]="COMMITTED"
    record["head_sha"]=new_head
    record["recovery_state"]="RECOVERING"
    record["recovery_result"]="PRESERVED_COMMIT_RECOVERED"
    cap["committed_head_sha"]=new_head
    cap["outcome"]="COMMITTED_EXACT"
    return "PRESERVED_COMMIT_RECOVERED"

def reconcile_consumed_recovery(ws:Path,record:dict)->str:
    """Adopt only a provably exact commit left by an interrupted recovery mutation."""
    cap=record.get("preserved_recovery_capability")
    if not isinstance(cap,dict) or cap.get("state")!="CONSUMED":
        return "CONSUMED_RECOVERY_NOT_APPLICABLE"
    if record.get("execution_stage")=="COMMITTED" and record.get("head_sha"):
        return "CONSUMED_RECOVERY_ALREADY_DURABLE"
    expected=record.get("preserved_work_evidence",{})
    base=record.get("base_sha"); branch_expected=record.get("branch")
    if not base or not branch_expected or not expected.get("diff_sha256"):
        return "CONSUMED_RECOVERY_EVIDENCE_MISSING"
    status=subprocess.run(["git","status","--porcelain"],cwd=ws,text=True,capture_output=True,check=False)
    head=subprocess.run(["git","rev-parse","HEAD"],cwd=ws,text=True,capture_output=True,check=False)
    branch=subprocess.run(["git","branch","--show-current"],cwd=ws,text=True,capture_output=True,check=False)
    if status.returncode or head.returncode or branch.returncode:
        return "CONSUMED_RECOVERY_WORKSPACE_UNREADABLE"
    if branch.stdout.strip()!=branch_expected:
        return "CONSUMED_RECOVERY_BRANCH_MISMATCH"
    current_head=head.stdout.strip()
    if current_head==base:
        return "CONSUMED_RECOVERY_NO_COMMIT"
    if status.stdout.strip():
        return "CONSUMED_RECOVERY_DIRTY_AFTER_COMMIT"
    ancestor=subprocess.run(["git","merge-base","--is-ancestor",base,current_head],cwd=ws,text=True,capture_output=True,check=False)
    if ancestor.returncode:
        return "CONSUMED_RECOVERY_NON_DESCENDANT"
    diff=subprocess.run(["git","diff","--binary",base,current_head],cwd=ws,text=True,capture_output=True,check=False)
    import hashlib
    digest=hashlib.sha256(diff.stdout.encode("utf-8",errors="replace")).hexdigest() if not diff.returncode else None
    if digest!=expected.get("diff_sha256"):
        return "CONSUMED_RECOVERY_COMMIT_MISMATCH"
    record["execution_stage"]="COMMITTED"
    record["head_sha"]=current_head
    record["recovery_state"]="RECOVERING"
    record["recovery_result"]="CONSUMED_RECOVERY_COMMIT_ADOPTED"
    cap["outcome"]="COMMITTED_EXACT_RECONCILED"
    cap["committed_head_sha"]=current_head
    return "CONSUMED_RECOVERY_COMMIT_ADOPTED"

def reconcile_consumed_staged_work(ws:Path,record:dict)->str:
    """Recognize exact staged work after consumed authority; never reuse old authority."""
    cap=record.get("preserved_recovery_capability")
    if not isinstance(cap,dict) or cap.get("state")!="CONSUMED":
        return "STAGED_RECOVERY_NOT_APPLICABLE"
    base=record.get("base_sha"); expected=record.get("preserved_work_evidence",{})
    if not base or not record.get("branch") or not expected.get("diff_sha256"):
        return "STAGED_RECOVERY_EVIDENCE_MISSING"
    head=subprocess.run(["git","rev-parse","HEAD"],cwd=ws,text=True,capture_output=True,check=False)
    branch=subprocess.run(["git","branch","--show-current"],cwd=ws,text=True,capture_output=True,check=False)
    if head.returncode or branch.returncode:
        return "STAGED_RECOVERY_WORKSPACE_UNREADABLE"
    if head.stdout.strip()!=base:
        return "STAGED_RECOVERY_HEAD_MOVED"
    if branch.stdout.strip()!=record["branch"]:
        return "STAGED_RECOVERY_BRANCH_MISMATCH"
    diff=subprocess.run(["git","diff","--binary",base],cwd=ws,text=True,capture_output=True,check=False)
    cached=subprocess.run(["git","diff","--cached","--binary",base],cwd=ws,text=True,capture_output=True,check=False)
    unstaged=subprocess.run(["git","diff","--binary"],cwd=ws,text=True,capture_output=True,check=False)
    if diff.returncode or cached.returncode or unstaged.returncode:
        return "STAGED_RECOVERY_DIFF_UNREADABLE"
    import hashlib
    full_digest=hashlib.sha256(diff.stdout.encode("utf-8",errors="replace")).hexdigest()
    cached_digest=hashlib.sha256(cached.stdout.encode("utf-8",errors="replace")).hexdigest()
    if full_digest!=expected["diff_sha256"] or cached_digest!=expected["diff_sha256"] or unstaged.stdout:
        return "STAGED_RECOVERY_NOT_EXACTLY_STAGED"
    cap["outcome"]="EXACT_STAGED_RECONCILED"
    record["recovery_state"]="QUARANTINED"
    record["recovery_result"]="CONSUMED_RECOVERY_EXACT_STAGED"
    return "CONSUMED_RECOVERY_EXACT_STAGED"

def issue_staged_commit_capability(record:dict)->dict|None:
    """Issue replacement authority for commit only after exact staged reconciliation."""
    old=record.get("preserved_recovery_capability")
    evidence=record.get("preserved_work_evidence",{})
    if record.get("recovery_result")!="CONSUMED_RECOVERY_EXACT_STAGED" or not isinstance(old,dict):
        return None
    if old.get("state")!="CONSUMED" or old.get("outcome")!="EXACT_STAGED_RECONCILED":
        return None
    if not old.get("capability_id") or not evidence.get("diff_sha256"):
        return None
    import secrets
    return {"capability_id":secrets.token_urlsafe(18),"state":"ISSUED","scope":"COMMIT_ONLY",
            "parent_capability_id":old["capability_id"],"repository":record.get("repository"),
            "base_sha":record.get("base_sha"),"branch":record.get("branch"),
            "preserved_diff_sha256":evidence["diff_sha256"],"issued_at":datetime.now(UTC).isoformat()}

def commit_reconciled_staged_work(ws:Path,record:dict,reconcile_fn=None)->str:
    """Consume commit-only replacement authority; never stage/reset preserved work."""
    reconcile_fn = reconcile_fn or reconcile_consumed_staged_work
    cap=record.get("staged_commit_capability")
    if not isinstance(cap,dict) or cap.get("state")!="ISSUED" or cap.get("scope")!="COMMIT_ONLY":
        return "STAGED_COMMIT_CAPABILITY_UNAVAILABLE"
    old=record.get("preserved_recovery_capability",{}); evidence=record.get("preserved_work_evidence",{})
    bindings={"parent_capability_id":old.get("capability_id"),"repository":record.get("repository"),
              "base_sha":record.get("base_sha"),"branch":record.get("branch"),
              "preserved_diff_sha256":evidence.get("diff_sha256")}
    if any(cap.get(k)!=v for k,v in bindings.items()):
        return "STAGED_COMMIT_CAPABILITY_BINDING_MISMATCH"
    if reconcile_fn(ws,record)!="CONSUMED_RECOVERY_EXACT_STAGED":
        return "STAGED_COMMIT_IDENTITY_CHANGED"
    cap["state"]="CONSUMED"; cap["consumed_at"]=datetime.now(UTC).isoformat()
    commit=subprocess.run(["git","commit","-m","chore: recover validated autonomous work"],cwd=ws,text=True,capture_output=True,check=False)
    if commit.returncode:
        cap["outcome"]="COMMIT_FAILED"; return "STAGED_COMMIT_FAILED"
    head=subprocess.run(["git","rev-parse","HEAD"],cwd=ws,text=True,capture_output=True,check=False)
    branch=subprocess.run(["git","branch","--show-current"],cwd=ws,text=True,capture_output=True,check=False)
    diff=subprocess.run(["git","diff","--binary",record["base_sha"],head.stdout.strip()],cwd=ws,text=True,capture_output=True,check=False) if not head.returncode else None
    import hashlib
    digest=hashlib.sha256(diff.stdout.encode("utf-8",errors="replace")).hexdigest() if diff and not diff.returncode else None
    if head.returncode or branch.returncode or branch.stdout.strip()!=record["branch"] or digest!=evidence.get("diff_sha256"):
        cap["outcome"]="POST_COMMIT_PROOF_FAILED"; return "STAGED_COMMIT_PROOF_FAILED"
    record["execution_stage"]="COMMITTED"; record["head_sha"]=head.stdout.strip()
    record["recovery_state"]="RECOVERING"; record["recovery_result"]="STAGED_COMMIT_RECOVERED"
    cap["outcome"]="COMMITTED_EXACT"; cap["committed_head_sha"]=record["head_sha"]
    return "STAGED_COMMIT_RECOVERED"

def pending_recovery_grants(
    state_root:Path,
    newly_abandoned:list[str]|None=None,
    now:datetime|None=None,
    max_batch:int=8,
)->list[str]:
    """Return a bounded due batch of abandoned recovery obligations."""
    if max_batch < 1:
        return []
    path=state_root/"hands-off-grant-lifecycle.json"
    lifecycle_lock=acquire_lifecycle_lock(path)
    try:
        try:
            payload=json.loads(path.read_text(encoding="utf-8"))
            grants=payload.get("grants",{})
        except (OSError,json.JSONDecodeError,TypeError):
            return list(dict.fromkeys(newly_abandoned or []))[:max_batch]
        current=now or datetime.now(UTC)
        pending=list(dict.fromkeys(newly_abandoned or []))
        changed=False
        for grant_id,record in grants.items():
            if len(pending)>=max_batch:
                break
            if not isinstance(record,dict) or record.get("state")!="ABANDONED":
                continue
            recovery_state=record.get("recovery_state")
            eligible=recovery_state not in {"RECOVERED","QUARANTINED"}
            if (recovery_state=="QUARANTINED" and record.get("execution_stage")=="COMMITTED"
                and record.get("head_sha") and record.get("recovery_result") in {
                    "PRESERVED_COMMIT_RECOVERED","CONSUMED_RECOVERY_COMMIT_ADOPTED","STAGED_COMMIT_RECOVERED"
                }):
                record["recovery_state"]="RECOVERING"
                recovery_state="RECOVERING"
                eligible=True
                changed=True
            if not eligible or grant_id in pending:
                continue
            next_attempt=record.get("recovery_next_attempt_at")
            if next_attempt:
                try:
                    due=datetime.fromisoformat(next_attempt)
                    if due.tzinfo is None:
                        due=due.replace(tzinfo=UTC)
                    if current<due:
                        continue
                except (TypeError,ValueError):
                    pass
            pending.append(grant_id)
        if changed:
            payload["grants"]=grants
            atomic_lifecycle_json(path,payload)
        return pending[:max_batch]
    finally:
        release_lifecycle_lock(lifecycle_lock)

def verify_durable_pr(repo:str,ws:Path,pr_number:int,expected_head:str)->str:
    """Recover PR_OPEN only by exact durable PR number and exact recorded head."""
    viewed=subprocess.run(["gh","pr","view",str(pr_number),"--repo",repo,
                           "--json","number,state,headRefOid"],cwd=ws,text=True,capture_output=True,check=False)
    if viewed.returncode:
        return "RECOVERY_DURABLE_PR_UNREADABLE"
    try:
        pr=json.loads(viewed.stdout)
    except (json.JSONDecodeError,TypeError):
        return "RECOVERY_DURABLE_PR_UNREADABLE"
    if pr.get("number")!=pr_number or pr.get("headRefOid")!=expected_head:
        return "RECOVERY_DURABLE_PR_IDENTITY_MISMATCH"
    state=str(pr.get("state","")).upper()
    if state=="OPEN":
        return "RECOVERY_DURABLE_PR_HANDOFF"
    if state=="MERGED":
        return "RECOVERY_DURABLE_PR_MERGED"
    if state=="CLOSED":
        return "RECOVERY_DURABLE_PR_CLOSED_UNMERGED"
    return "RECOVERY_DURABLE_PR_STATE_UNKNOWN"

def begin_recovery_generation(
    path:Path,grant_id:str,expected:dict,*,liveness_probe=None,fingerprint_probe=None
)->tuple[str,dict]|None:
    """Reserve one exact lifecycle generation before recovery may perform side effects."""
    liveness_probe = liveness_probe or worker_liveness
    fingerprint_probe = fingerprint_probe or process_start_fingerprint
    lock=acquire_lifecycle_lock(path)
    try:
        try:
            payload=json.loads(path.read_text(encoding="utf-8"))
            current=payload["grants"][grant_id]
        except (OSError,json.JSONDecodeError,KeyError,TypeError):
            return None
        if current!=expected:
            return None
        if current.get("recovery_generation"):
            owner=current.get("recovery_generation_worker") or current.get("recovery_owner")
            started=current.get("recovery_generation_started_at")
            stale=False
            try:
                when=datetime.fromisoformat(started)
                if when.tzinfo is None: when=when.replace(tzinfo=UTC)
                stale=(datetime.now(UTC)-when).total_seconds()>RECOVERY_GENERATION_LEASE_SECONDS
            except (TypeError,ValueError):
                stale=True
            if not stale or liveness_probe(owner)!="DEAD":
                return None
            current=dict(current)
            current.pop("recovery_generation",None)
            current.pop("recovery_generation_started_at",None)
            current.pop("recovery_owner",None)
            current.pop("recovery_generation_worker",None)
            current["recovery_generation_reclaimed_at"]=datetime.now(UTC).isoformat()
        target_repos=set(current.get("authorized_repositories",[]))
        if current.get("repository"):
            target_repos.add(current["repository"])
        for other_id,other in payload.get("grants",{}).items():
            if other_id==grant_id or not isinstance(other,dict):
                continue
            other_repos=set(other.get("authorized_repositories",[]))
            if other.get("repository"):
                other_repos.add(other["repository"])
            owns=other.get("state") in {"ISSUED","CLAIMED","QUARANTINED"} or bool(other.get("recovery_generation"))
            if owns and target_repos.intersection(other_repos):
                return None
        token=uuid.uuid4().hex
        reserved=dict(current)
        reserved["recovery_generation"]=token
        reserved["recovery_generation_started_at"]=datetime.now(UTC).isoformat()
        reserved["recovery_generation_worker"]={"host":socket.gethostname(),"pid":os.getpid(),
                                                 "process_start_fingerprint":fingerprint_probe()}
        reserved["recovery_version"]=int(current.get("recovery_version",0))+1
        payload["grants"][grant_id]=reserved
        try:
            atomic_lifecycle_json(path,payload)
        except PermissionError:
            return None
        return token,reserved
    finally:
        release_lifecycle_lock(lock)

def commit_recovery_record(path:Path,grant_id:str,expected:dict,updated:dict)->bool:
    """Commit only the exact reserved recovery generation; compare the complete record."""
    lock=acquire_lifecycle_lock(path)
    try:
        try:
            payload=json.loads(path.read_text(encoding="utf-8"))
            current=payload["grants"][grant_id]
        except (OSError,json.JSONDecodeError,KeyError,TypeError):
            return False
        if current!=expected:
            return False
        final=dict(updated)
        final.pop("recovery_generation",None)
        final.pop("recovery_generation_started_at",None)
        final.pop("recovery_generation_worker",None)
        final.pop("recovery_generation_unknown_checks",None)
        payload["grants"][grant_id]=final
        atomic_lifecycle_json(path,payload)
        return True
    finally:
        release_lifecycle_lock(lock)

def reconcile_abandoned_claims(workspace_root:Path,state_root:Path,grant_ids:list[str],now:datetime|None=None)->dict[str,str]:
    """Revisit durable recovery obligations until recovered or explicitly quarantined."""
    path=state_root/"hands-off-grant-lifecycle.json"
    if not grant_ids or not path.exists():
        return {}
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
        grants=payload["grants"]
    except (OSError,json.JSONDecodeError,KeyError,TypeError):
        return {grant_id:"LIFECYCLE_UNREADABLE" for grant_id in grant_ids}
    outcomes={}
    current=now or datetime.now(UTC)
    expected_records={}
    for grant_id in grant_ids:
        original=dict(grants.get(grant_id,{}))
        reservation=begin_recovery_generation(path,grant_id,original)
        if reservation is None:
            outcomes[grant_id]="RECOVERY_GENERATION_BUSY_OR_STALE"
            continue
        _,record=reservation
        grants[grant_id]=record
        expected_records[grant_id]=dict(record)
        attempts=int(record.get("recovery_attempts",0))
        next_attempt=record.get("recovery_next_attempt_at")
        if next_attempt:
            try:
                due=datetime.fromisoformat(next_attempt)
                if due.tzinfo is None: due=due.replace(tzinfo=UTC)
                if current<due:
                    outcomes[grant_id]="RECOVERY_BACKOFF"
                    continue
            except (TypeError,ValueError):
                pass
        if attempts>=5:
            record["recovery_state"]="QUARANTINED"
            record["recovery_result"]="RECOVERY_RETRY_EXHAUSTED"
            outcomes[grant_id]="RECOVERY_RETRY_EXHAUSTED"
            continue
        record["recovery_attempts"]=attempts+1
        record["recovery_last_attempt_at"]=current.isoformat()
        repos=record.get("authorized_repositories",[])
        repo=record.get("repository")
        candidates=[repo] if repo else repos
        if len(candidates)!=1:
            outcomes[grant_id]="RECOVERY_IDENTITY_AMBIGUOUS"
        else:
            repo=candidates[0]; ws=workspace_root/repo.split("/",1)[-1]
            if not ws.is_dir():
                outcomes[grant_id]="WORKSPACE_MISSING"
            else:
                outcomes[grant_id]=None
        if outcomes.get(grant_id) is not None:
            record["recovery_result"]=outcomes[grant_id]
            delay=min(3600,60*(2**attempts))
            record["recovery_next_attempt_at"]=(current+timedelta(seconds=delay)).isoformat()
            continue
        stage=record.get("execution_stage")
        durable_branch=record.get("branch")
        durable_head=record.get("head_sha")
        durable_base=record.get("base_sha")
        if stage in {"BASE_VERIFIED","BRANCH_CREATED"} and durable_base:
            status=subprocess.run(["git","status","--porcelain"],cwd=ws,text=True,capture_output=True,check=False)
            head_now=subprocess.run(["git","rev-parse","HEAD"],cwd=ws,text=True,capture_output=True,check=False)
            branch_now=subprocess.run(["git","branch","--show-current"],cwd=ws,text=True,capture_output=True,check=False)
            if status.returncode or head_now.returncode or branch_now.returncode:
                outcomes[grant_id]="RECOVERY_EARLY_STAGE_WORKSPACE_UNREADABLE"
            elif status.stdout.strip():
                evidence=preserved_work_evidence(ws,durable_base)
                if builder_artifacts_only(evidence):
                    outcomes[grant_id]="RECOVERY_EARLY_STAGE_BUILDER_ARTIFACTS_ONLY"
                    record["recovery_state"]="RECOVERED"
                    record["recovered_at"]=current.isoformat()
                    record["ignored_builder_artifacts"]=[item["path"] for item in evidence["untracked"]]
                else:
                    outcomes[grant_id]="RECOVERY_EARLY_STAGE_DIRTY_PRESERVED"
                    record["recovery_state"]="QUARANTINED"
                    record["recovery_quarantine_reason"]="UNCOMMITTED_WORK_REQUIRES_PRESERVATION"
                    record["preserved_work_evidence"]=evidence
            elif head_now.stdout.strip()!=durable_base:
                outcomes[grant_id]="RECOVERY_EARLY_STAGE_HEAD_DRIFT"
                record["recovery_state"]="QUARANTINED"
                record["recovery_quarantine_reason"]="UNRECORDED_COMMIT_REQUIRES_PRESERVATION"
                record["preserved_work_evidence"]=preserved_work_evidence(ws,durable_base)
            elif stage=="BRANCH_CREATED" and durable_branch and branch_now.stdout.strip()!=durable_branch:
                outcomes[grant_id]="RECOVERY_EARLY_STAGE_BRANCH_DRIFT"
                record["recovery_state"]="QUARANTINED"
                record["recovery_quarantine_reason"]="BRANCH_IDENTITY_MISMATCH"
                record["preserved_work_evidence"]=preserved_work_evidence(ws,durable_base)
            else:
                outcomes[grant_id]="RECOVERY_EARLY_STAGE_NO_PRODUCT_WORK"
                record["recovery_state"]="RECOVERED"
                record["recovered_at"]=current.isoformat()
            record["recovery_result"]=outcomes[grant_id]
            if record.get("recovery_state") in {"RECOVERED","QUARANTINED"}:
                record.pop("recovery_next_attempt_at",None)
            else:
                delay=min(3600,60*(2**attempts))
                record["recovery_next_attempt_at"]=(current+timedelta(seconds=delay)).isoformat()
            continue
        if stage in {"PUSHED","PR_OPEN"} and durable_branch and durable_head:
            remote=subprocess.run(["git","ls-remote","--heads","origin",durable_branch],cwd=ws,text=True,capture_output=True,check=False)
            remote_head=remote.stdout.split()[0] if remote.returncode==0 and remote.stdout.strip() else ""
            if remote_head!=durable_head:
                outcomes[grant_id]="RECOVERY_DURABLE_REMOTE_HEAD_MISMATCH"
            elif stage=="PR_OPEN":
                pr_number=record.get("pr_number")
                if not isinstance(pr_number,int) or pr_number<1:
                    outcomes[grant_id]="RECOVERY_DURABLE_PR_IDENTITY_REQUIRED"
                else:
                    outcomes[grant_id]=verify_durable_pr(repo,ws,pr_number,durable_head)
                if outcomes[grant_id] in {"RECOVERY_DURABLE_PR_HANDOFF","RECOVERY_DURABLE_PR_MERGED"}:
                    record["recovery_state"]="RECOVERED"
                    record["recovered_at"]=current.isoformat()
                elif outcomes[grant_id]=="RECOVERY_DURABLE_PR_CLOSED_UNMERGED":
                    record["recovery_state"]="QUARANTINED"
                    record["recovery_quarantine_reason"]="PR_CLOSED_WITHOUT_MERGE"
            else:
                evidence={"head_sha":durable_head,"remote_head_sha":record.get("remote_head_sha") or durable_head}
                outcomes[grant_id]=resume_orphan_branch(repo,ws,durable_branch,"ORPHAN_PUSHED_NO_PR",evidence)
                if outcomes[grant_id]=="RECOVERY_PR_OPENED":
                    record["recovery_state"]="RECOVERED"
                    record["recovered_at"]=current.isoformat()
            record["recovery_result"]=outcomes[grant_id]
            if record.get("recovery_state")=="RECOVERED":
                record.pop("recovery_next_attempt_at",None)
            else:
                delay=min(3600,60*(2**attempts))
                record["recovery_next_attempt_at"]=(current+timedelta(seconds=delay)).isoformat()
            continue
        if stage=="COMMITTED" and durable_branch and durable_head:
            branch_now=subprocess.run(["git","branch","--show-current"],cwd=ws,text=True,capture_output=True,check=False)
            head_now=subprocess.run(["git","rev-parse","HEAD"],cwd=ws,text=True,capture_output=True,check=False)
            if branch_now.returncode or head_now.returncode or branch_now.stdout.strip()!=durable_branch or head_now.stdout.strip()!=durable_head:
                outcomes[grant_id]="RECOVERY_DURABLE_LOCAL_HEAD_MISMATCH"
            else:
                evidence={"head_sha":durable_head}
                outcomes[grant_id]=resume_orphan_branch(repo,ws,durable_branch,"ORPHAN_LOCAL_COMMIT_UNPUSHED",evidence)
                if outcomes[grant_id]=="RECOVERY_PR_OPENED":
                    record["recovery_state"]="RECOVERED"
                    record["recovered_at"]=current.isoformat()
            record["recovery_result"]=outcomes[grant_id]
            if record.get("recovery_state")=="RECOVERED":
                record.pop("recovery_next_attempt_at",None)
            else:
                delay=min(3600,60*(2**attempts))
                record["recovery_next_attempt_at"]=(current+timedelta(seconds=delay)).isoformat()
            continue
        status=subprocess.run(["git","status","--porcelain"],cwd=ws,text=True,capture_output=True,check=False)
        branch=subprocess.run(["git","branch","--show-current"],cwd=ws,text=True,capture_output=True,check=False)
        if status.returncode or branch.returncode:
            outcomes[grant_id]="WORKSPACE_UNREADABLE"; continue
        dirty=bool(status.stdout.strip())
        current_branch=branch.stdout.strip()
        if dirty:
            outcomes[grant_id]="DIRTY_WORKSPACE_QUARANTINED"
        elif current_branch.startswith("actor/autonomous-"):
            classification,evidence=classify_orphan_branch(repo,ws,current_branch)
            outcomes[grant_id]=classification
            record["recovery_evidence"]=evidence
            if classification in {"ORPHAN_LOCAL_COMMIT_UNPUSHED","ORPHAN_PUSHED_NO_PR"}:
                classification=resume_orphan_branch(repo,ws,current_branch,classification,evidence)
                outcomes[grant_id]=classification
            if classification in {"ORPHAN_NO_DIFF","ORPHAN_PR_RECOVERABLE","RECOVERY_PR_OPENED"}:
                record["recovery_state"]="RECOVERED"
                record["recovered_at"]=datetime.now(UTC).isoformat()
        else:
            outcomes[grant_id]="CLEAN_WORKSPACE_RECOVERED"
            record["recovery_state"]="RECOVERED"
            record["recovered_at"]=datetime.now(UTC).isoformat()
        record["recovery_result"]=outcomes[grant_id]
        if record.get("recovery_state") not in {"RECOVERED","QUARANTINED"}:
            delay=min(3600,60*(2**attempts))
            record["recovery_next_attempt_at"]=(current+timedelta(seconds=delay)).isoformat()
        else:
            record.pop("recovery_next_attempt_at",None)
    for grant_id in grant_ids:
        if grant_id not in grants or grant_id not in expected_records:
            continue
        if not commit_recovery_record(path,grant_id,expected_records[grant_id],grants[grant_id]):
            outcomes[grant_id]="RECOVERY_CONCURRENT_STATE_CHANGE"
    return outcomes

def recheck_preserved_work_quarantines(
    workspace_root:Path,state_root:Path,*,revalidate_fn=None,validate_fn=None,
    commit_fn=None,reconcile_consumed_fn=None,reconcile_staged_fn=None,
    issue_staged_fn=None,staged_commit_fn=None
)->dict[str,str]:
    """Continuously revalidate preserved crash work without mutating it."""
    revalidate_fn = revalidate_fn or revalidate_preserved_work
    validate_fn = validate_fn or validate_preserved_work
    commit_fn = commit_fn or commit_preserved_work
    reconcile_consumed_fn = reconcile_consumed_fn or reconcile_consumed_recovery
    reconcile_staged_fn = reconcile_staged_fn or reconcile_consumed_staged_work
    issue_staged_fn = issue_staged_fn or issue_staged_commit_capability
    staged_commit_fn = staged_commit_fn or commit_reconciled_staged_work
    path=state_root/"hands-off-grant-lifecycle.json"
    if not path.exists(): return {}
    try:
        payload=json.loads(path.read_text(encoding="utf-8")); grants=payload.get("grants",{})
    except (OSError,json.JSONDecodeError,TypeError):
        return {}
    outcomes={}; changed=False
    expected_records={}
    for grant_id,record in list(grants.items()):
        if not isinstance(record,dict) or record.get("recovery_state")!="QUARANTINED":
            continue
        if record.get("recovery_quarantine_reason") not in {
            "UNCOMMITTED_WORK_REQUIRES_PRESERVATION","UNRECORDED_COMMIT_REQUIRES_PRESERVATION","BRANCH_IDENTITY_MISMATCH"
        }: continue
        original=dict(record)
        reservation=begin_recovery_generation(path,grant_id,original)
        if reservation is None:
            outcomes[grant_id]="PRESERVED_RECOVERY_GENERATION_BUSY_OR_STALE"
            continue
        _,record=reservation
        grants[grant_id]=record
        expected_records[grant_id]=dict(record)
        repo=record.get("repository")
        if not repo:
            outcomes[grant_id]="PRESERVED_REPOSITORY_MISSING"
            record["preserved_work_revalidation"]="PRESERVED_REPOSITORY_MISSING"
            changed=True
            continue
        ws=workspace_root/repo.split("/",1)[-1]
        if ws.is_dir() and record.get("preserved_recovery_capability",{}).get("state")=="CONSUMED":
            reconciled=reconcile_consumed_fn(ws,record)
            if reconciled in {"CONSUMED_RECOVERY_COMMIT_ADOPTED","CONSUMED_RECOVERY_ALREADY_DURABLE"}:
                outcomes[grant_id]=reconciled
                changed=True
                continue
            if reconciled=="CONSUMED_RECOVERY_NO_COMMIT":
                staged=reconcile_staged_fn(ws,record)
                if staged=="CONSUMED_RECOVERY_EXACT_STAGED":
                    if not record.get("staged_commit_capability"):
                        replacement=issue_staged_fn(record)
                        if replacement: record["staged_commit_capability"]=replacement
                    if record.get("staged_commit_capability",{}).get("state")=="ISSUED":
                        staged=staged_commit_fn(ws,record)
                    outcomes[grant_id]=staged
                    changed=True
                    continue
        outcome=revalidate_fn(ws,record) if ws.is_dir() else "PRESERVED_WORKSPACE_MISSING"
        outcomes[grant_id]=outcome
        record["preserved_work_revalidation"]=outcome
        record["preserved_work_revalidated_at"]=datetime.now(UTC).isoformat()
        if outcome=="PRESERVED_WORK_EXACT":
            evidence=record.get("preserved_work_evidence",{})
            if builder_artifacts_only(evidence):
                outcomes[grant_id]="PRESERVED_BUILDER_ARTIFACTS_RELEASED"
                record["preserved_work_revalidation"]=outcomes[grant_id]
                record["recovery_state"]="RECOVERED"
                record["recovered_at"]=datetime.now(UTC).isoformat()
                record["ignored_builder_artifacts"]=[item["path"] for item in evidence["untracked"]]
                changed=True
                continue
            validation=validate_fn(ws,record)
            record["preserved_work_validation"]=validation
            record["preserved_work_validated_at"]=datetime.now(UTC).isoformat()
            if validation=="PRESERVED_VALIDATION_PASSED" and not record.get("preserved_recovery_capability"):
                capability=issue_preserved_recovery_capability(record)
                if capability:
                    record["preserved_recovery_capability"]=capability
            if validation=="PRESERVED_VALIDATION_PASSED" and record.get("preserved_recovery_capability",{}).get("state")=="ISSUED":
                commit_outcome=commit_fn(ws,record)
                record["preserved_work_commit_result"]=commit_outcome
                outcomes[grant_id]=commit_outcome
            else:
                outcomes[grant_id]=validation
        changed=True
    if changed:
        for grant_id in list(outcomes):
            record=grants.get(grant_id)
            if isinstance(record,dict) and record!=expected_records.get(grant_id):
                if not commit_recovery_record(path,grant_id,expected_records.get(grant_id,{}),record):
                    outcomes[grant_id]="PRESERVED_RECOVERY_CONCURRENT_STATE_CHANGE"
    return outcomes

