from __future__ import annotations
from collections import Counter
from typing import Any

SAFE_PREVENTION_CLASSES={"FORMAT","IMPORT_SORT","LINT_SAFE_FIX"}
MIN_VERIFIED_RECURRENCES=3

def build_prevention_plan(events:list[dict[str,Any]],policies:dict[str,dict[str,Any]])->dict[str,Any]:
    counts:Counter[tuple[str,str]]=Counter()
    refs:dict[tuple[str,str],list[str]]={}
    for event in events:
        if event.get("evidence_state")!="VERIFIED":
            continue
        repo=event.get("repository"); klass=event.get("failure_class")
        if not isinstance(repo,str) or klass not in SAFE_PREVENTION_CLASSES:
            continue
        key=(repo,klass); counts[key]+=1
        if isinstance(event.get("source_ref"),str):
            refs.setdefault(key,[]).append(event["source_ref"])
    candidates=[]
    for (repo,klass),count in sorted(counts.items()):
        prevention=policies.get(repo,{}).get("regression_prevention",{})
        approved=prevention.get("approved_classes",[])
        command=prevention.get("commands",{}).get(klass)
        if count<MIN_VERIFIED_RECURRENCES:
            state="INSUFFICIENT_EVIDENCE"; reason=f"{count} verified recurrence(s); need {MIN_VERIFIED_RECURRENCES}"
        elif klass not in approved or not isinstance(command,list) or not command:
            state="BLOCKED"; reason="repository has not explicitly approved an earlier prevention command"
        else:
            state="READY"; reason="recurrence threshold met and repository prevention authority is explicit"
        candidates.append({"repository":repo,"failure_class":klass,"verified_recurrence_count":count,
            "evidence_refs":sorted(set(refs.get((repo,klass),[]))),"state":state,"reason":reason,
            "recommended_stage":prevention.get("stage","pre_commit"),"command":command if state=="READY" else None,
            "automatic_mutation_authority":False})
    return {"schema_version":1,"minimum_verified_recurrences":MIN_VERIFIED_RECURRENCES,"candidates":candidates,
        "safety_boundaries":["only VERIFIED repair evidence counts","only deterministic safe classes are eligible",
        "repository-explicit prevention authority is required","engine recommends guardrails but never edits product repositories itself",
        "security, behavioral tests, governance, provenance, Product Reality, environment, and unknown failures are never auto-promoted"]}
