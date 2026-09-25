from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import subprocess

@dataclass(frozen=True)
class Verdict:
    status:str
    reason:str|None=None

def review_candidate(ws:Path,base_sha:str,head_sha:str,authority:dict,changed_paths:list[str],preflight_returncode:int)->Verdict:
    """Deterministic reviewer independent of the implementation agent."""
    if preflight_returncode!=0:
        return Verdict("REJECT","PREFLIGHT_NOT_GREEN")
    acceptance=authority.get("acceptance")
    allowed=authority.get("allowed_paths",[])
    forbidden=authority.get("forbidden_paths",[])
    if not isinstance(acceptance,list) or not acceptance or not all(isinstance(x,str) and x.strip() for x in acceptance):
        return Verdict("REJECT","ACCEPTANCE_AUTHORITY_INVALID")
    if not isinstance(allowed,list) or not isinstance(forbidden,list):
        return Verdict("REJECT","PATH_AUTHORITY_INVALID")
    def match(path,prefix):
        path=path.replace(chr(92),"/").strip("/")
        prefix=prefix.replace(chr(92),"/").strip("/")
        return path==prefix or path.startswith(prefix+"/")
    for path in changed_paths:
        if any(match(path,x) for x in forbidden):
            return Verdict("REJECT",f"FORBIDDEN_PATH:{path}")
        if allowed and not any(match(path,x) for x in allowed):
            return Verdict("REJECT",f"OUTSIDE_AUTHORITY:{path}")
    actual=subprocess.run(["git","rev-parse","HEAD"],cwd=ws,text=True,capture_output=True,check=False)
    if actual.returncode or actual.stdout.strip()!=head_sha:
        return Verdict("REJECT","HEAD_PROVENANCE_MISMATCH")
    ancestor=subprocess.run(["git","merge-base","--is-ancestor",base_sha,head_sha],cwd=ws,check=False)
    if ancestor.returncode:
        return Verdict("REJECT","BASE_PROVENANCE_MISMATCH")
    diff=subprocess.run(["git","diff","--name-only",base_sha,head_sha],cwd=ws,text=True,capture_output=True,check=False)
    if diff.returncode or sorted(x for x in diff.stdout.splitlines() if x)!=sorted(changed_paths):
        return Verdict("REJECT","DIFF_PROVENANCE_MISMATCH")
    return Verdict("APPROVE")
