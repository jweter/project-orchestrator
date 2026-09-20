#!/usr/bin/env python3
"""Validate a machine-readable project capability graph without inventing direction."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path

STATES={"PLANNED","IN_PROGRESS","BLOCKED","IMPLEMENTED_UNVERIFIED","VERIFIED_COMPLETE"}

def validate(payload:dict)->dict:
    issues=[]; caps=payload.get("capabilities")
    if not isinstance(caps,list) or not caps:
        issues.append({"code":"CAPABILITIES_REQUIRED"})
        return {"schema_version":1,"status":"DIRECTION_REQUIRED","issues":issues,"eligible":[],"remaining":[]}
    ids=[c.get("id") for c in caps if isinstance(c,dict)]
    if len(ids)!=len(caps) or any(not isinstance(x,str) or not x.strip() for x in ids) or len(set(ids))!=len(ids):
        issues.append({"code":"INVALID_CAPABILITY_IDS"})
    known=set(x for x in ids if isinstance(x,str))
    for c in caps:
        if not isinstance(c,dict): continue
        cid=c.get("id","?")
        if not str(c.get("capability","")).strip(): issues.append({"code":"CAPABILITY_DESCRIPTION_REQUIRED","capability":cid})
        state=c.get("state")
        if state not in STATES: issues.append({"code":"INVALID_STATE","capability":cid})
        evidence=c.get("completion_evidence")
        if not isinstance(evidence,list) or not any(str(x).strip() for x in evidence):
            issues.append({"code":"COMPLETION_EVIDENCE_REQUIRED","capability":cid})
        deps=c.get("depends_on",[])
        if not isinstance(deps,list) or any(d not in known for d in deps):
            issues.append({"code":"UNKNOWN_DEPENDENCY","capability":cid})
        if cid in deps: issues.append({"code":"SELF_DEPENDENCY","capability":cid})
        if state=="VERIFIED_COMPLETE" and not c.get("verification"):
            issues.append({"code":"VERIFICATION_REQUIRED","capability":cid})
    # cycle detection
    graph={c.get("id"):c.get("depends_on",[]) for c in caps if isinstance(c,dict) and c.get("id") in known and isinstance(c.get("depends_on",[]),list)}
    visiting=set();done=set()
    def visit(n):
        if n in visiting:return True
        if n in done:return False
        visiting.add(n)
        if any(d in graph and visit(d) for d in graph.get(n,[])):return True
        visiting.remove(n);done.add(n);return False
    if any(visit(n) for n in list(graph) if n not in done): issues.append({"code":"DEPENDENCY_CYCLE"})
    complete={c.get("id") for c in caps if isinstance(c,dict) and c.get("state")=="VERIFIED_COMPLETE" and c.get("verification")}
    remaining=[c.get("id") for c in caps if isinstance(c,dict) and c.get("id") in known and c.get("id") not in complete]
    eligible=[c.get("id") for c in caps if isinstance(c,dict) and c.get("id") in remaining and c.get("state") not in {"BLOCKED","IMPLEMENTED_UNVERIFIED"} and all(d in complete for d in c.get("depends_on",[]))]
    roadmap=payload.get("roadmap")
    selected=None
    if not issues and remaining:
        if not isinstance(roadmap,list) or not roadmap:
            issues.append({"code":"ROADMAP_REQUIRED_FOR_REMAINING_WORK"})
        else:
            unknown=[x for x in roadmap if x not in known]
            duplicates=[x for x in set(roadmap) if roadmap.count(x)>1]
            if unknown: issues.append({"code":"ROADMAP_UNKNOWN_CAPABILITY","capabilities":unknown})
            if duplicates: issues.append({"code":"ROADMAP_DUPLICATE_CAPABILITY","capabilities":sorted(duplicates)})
            stale=[x for x in roadmap if x in complete]
            if stale: issues.append({"code":"ROADMAP_STALE_COMPLETE","capabilities":stale})
            omitted=[x for x in remaining if x not in roadmap]
            if omitted: issues.append({"code":"ROADMAP_OMITS_REMAINING","capabilities":omitted})
            if not issues:
                selected=next((x for x in roadmap if x in eligible),None)
                if eligible and selected is None:
                    issues.append({"code":"ROADMAP_HAS_NO_ELIGIBLE_PRIORITY","eligible":eligible})
    status="DIRECTION_REQUIRED" if issues else ("COMPLETE" if not remaining else ("READY" if eligible else "BLOCKED"))
    return {"schema_version":1,"status":status,"issues":issues,"eligible":eligible,"remaining":remaining,"selected":selected}

def main()->int:
    p=argparse.ArgumentParser();p.add_argument("graph",type=Path);a=p.parse_args()
    try: data=json.loads(a.graph.read_text(encoding="utf-8"));report=validate(data)
    except (OSError,json.JSONDecodeError) as e: report={"schema_version":1,"status":"DIRECTION_REQUIRED","issues":[{"code":"GRAPH_UNAVAILABLE","detail":str(e)}],"eligible":[],"remaining":[]}
    print(json.dumps(report,indent=2,sort_keys=True));return 0 if report["status"] in {"READY","COMPLETE","BLOCKED"} else 1
if __name__=="__main__":sys.exit(main())
