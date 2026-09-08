from __future__ import annotations
from collections import defaultdict, deque
import hashlib, json
from typing import Any

REQ={"project_id","work_id","title","slices"}
SREQ={"slice_id","repository","title","paths","dependencies"}
IMPORTANCE={"CRITICAL":3,"HIGH":2,"MEDIUM":1,"LOW":0}
LEVERAGE={"HIGH":2,"MEDIUM":1,"LOW":0}

def _id(x:dict[str,Any])->str:
    return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(",",":")).encode()).hexdigest()[:16]

def _overlap(a:list[str],b:list[str])->bool:
    return any(x==y or x.startswith(y.rstrip("/")+"/") or y.startswith(x.rstrip("/")+"/") for x in a for y in b)

def _validate(item:dict[str,Any])->None:
    miss=REQ-item.keys()
    if miss: raise ValueError("work item missing fields: "+", ".join(sorted(miss)))
    if not isinstance(item["slices"],list) or not item["slices"]: raise ValueError("work item must contain explicit candidate slices")
    ids:set[str]=set()
    for s in item["slices"]:
        m=SREQ-s.keys()
        if m: raise ValueError("slice missing fields: "+", ".join(sorted(m)))
        if s["slice_id"] in ids: raise ValueError("duplicate slice_id: "+s["slice_id"])
        ids.add(s["slice_id"])
        if not s["paths"] or not all(isinstance(x,str) and x for x in s["paths"]): raise ValueError(s["slice_id"]+": invalid paths")
        if not isinstance(s["dependencies"],list) or not all(isinstance(x,str) for x in s["dependencies"]): raise ValueError(s["slice_id"]+": invalid dependencies")
    for s in item["slices"]:
        unknown=set(s["dependencies"])-ids
        if unknown: raise ValueError(s["slice_id"]+": unknown dependencies: "+", ".join(sorted(unknown)))

def _levels(slices:list[dict[str,Any]])->dict[str,int]:
    by={s["slice_id"]:s for s in slices}
    inc={k:len(v["dependencies"]) for k,v in by.items()}
    kids:dict[str,list[str]]=defaultdict(list)
    for sid,s in by.items():
        for d in s["dependencies"]: kids[d].append(sid)
    q=deque(sorted(k for k,v in inc.items() if v==0)); level={k:0 for k in q}; seen=0
    while q:
        sid=q.popleft(); seen+=1
        for child in sorted(kids[sid]):
            inc[child]-=1; level[child]=max(level.get(child,0),level[sid]+1)
            if inc[child]==0: q.append(child)
    if seen!=len(by): raise ValueError("slice dependency graph contains a cycle")
    return level

def _unlock_counts(slices:list[dict[str,Any]])->dict[str,int]:
    kids:dict[str,set[str]]=defaultdict(set)
    for s in slices:
        for d in s["dependencies"]: kids[d].add(s["slice_id"])
    def count(start:str)->int:
        seen:set[str]=set(); stack=list(kids[start])
        while stack:
            x=stack.pop()
            if x in seen: continue
            seen.add(x); stack.extend(kids[x])
        return len(seen)
    return {s["slice_id"]:count(s["slice_id"]) for s in slices}

def decompose_work_item(item:dict[str,Any],*,live_snapshot:dict[str,Any]|None=None)->dict[str,Any]:
    """Rank explicit roadmap-derived candidate slices; never invent tasks from prose."""
    _validate(item); slices=item["slices"]; levels=_levels(slices); unlock=_unlock_counts(slices)
    active:dict[str,list[dict[str,Any]]]=defaultdict(list)
    if live_snapshot:
        for r in live_snapshot.get("repositories",[]):
            if isinstance(r.get("repository"),str): active[r["repository"]].extend(r.get("active_work",[]))
    out=[]
    for s in slices:
        conflicts=[w.get("branch") or f"issue-{w.get('issue')}" for w in active[s["repository"]] if _overlap(s["paths"],w.get("changed_files",[]))]
        reason=None
        if conflicts: reason="overlaps active work"
        elif item["project_id"]=="knowledge-engine" and s.get("shared_contract_change") and not s.get("compatibility_plan_clear"): reason="Knowledge Engine shared-contract slice lacks compatibility clearance"
        elif s.get("requires_product_reality_result") and not s.get("product_reality_verified"): reason="depends on unverified Product Reality"
        vw=s.get("verification_weight",1)
        if not isinstance(vw,int) or not 1<=vw<=5: raise ValueError(s["slice_id"]+": verification_weight must be 1..5")
        rev=s.get("reversibility","HIGH")
        if rev not in {"HIGH","MEDIUM","LOW"}: raise ValueError(s["slice_id"]+": invalid reversibility")
        importance=s.get("roadmap_importance","MEDIUM")
        if importance not in IMPORTANCE: raise ValueError(s["slice_id"]+": invalid roadmap_importance")
        reuse=s.get("reuse_leverage","LOW")
        parallel=s.get("parallelization_leverage","LOW")
        verification=s.get("verification_leverage","LOW")
        for name,value in [("reuse_leverage",reuse),("parallelization_leverage",parallel),("verification_leverage",verification)]:
            if value not in LEVERAGE: raise ValueError(s["slice_id"]+f": invalid {name}")
        reusable_assets=s.get("reusable_assets",[])
        if not isinstance(reusable_assets,list) or not all(isinstance(x,str) and x for x in reusable_assets):
            raise ValueError(s["slice_id"]+": reusable_assets must be a list of non-empty strings")
        explicit_unlocks=s.get("capability_unlocks",[])
        if not isinstance(explicit_unlocks,list) or not all(isinstance(x,str) and x for x in explicit_unlocks):
            raise ValueError(s["slice_id"]+": capability_unlocks must be a list of non-empty strings")
        score=unlock[s["slice_id"]]*10-levels[s["slice_id"]]*5-vw*2+{"HIGH":4,"MEDIUM":2,"LOW":0}[rev]
        leverage_key=(LEVERAGE[reuse],LEVERAGE[parallel],LEVERAGE[verification],len(reusable_assets),len(explicit_unlocks))
        out.append({**s,"decomposition_id":_id({"work_id":item["work_id"],"slice_id":s["slice_id"],"repository":s["repository"],"paths":sorted(s["paths"])}),
                    "dependency_level":levels[s["slice_id"]],"downstream_unlock_count":unlock[s["slice_id"]],
                    "active_conflicts":sorted(str(x) for x in conflicts),"state":"BLOCKED" if reason else "READY","blocked_reason":reason,
                    "roadmap_importance":importance,
                    "reuse_leverage":reuse,"parallelization_leverage":parallel,"verification_leverage":verification,
                    "reusable_assets":sorted(reusable_assets),"capability_unlocks":sorted(explicit_unlocks),
                    "structural_priority_score":score,
                    "selection_semantics":"roadmap importance first; leverage is a tiebreaker among otherwise eligible work",
                    "score_semantics":"ordinal structural score; not duration/ROI/probability",
                    "_selection_key":(-IMPORTANCE[importance],levels[s["slice_id"]],-unlock[s["slice_id"]],tuple(-x for x in leverage_key),-score,s["repository"],s["slice_id"])})
    ready=sorted((s for s in out if s["state"]=="READY"),key=lambda s:s["_selection_key"])
    waves=[]; remaining=ready[:]
    while remaining:
        minlevel=min(s["dependency_level"] for s in remaining); wave=[]; repos=set(); paths=[]
        for s in list(remaining):
            if s["dependency_level"]!=minlevel or s["repository"] in repos or _overlap(s["paths"],paths): continue
            wave.append(s); repos.add(s["repository"]); paths+=s["paths"]
        if not wave: break
        waves.append([s["slice_id"] for s in wave]); chosen={s["slice_id"] for s in wave}; remaining=[s for s in remaining if s["slice_id"] not in chosen]
    for s in out:
        s.pop("_selection_key",None)
    return {"schema_version":2,"project_id":item["project_id"],"work_id":item["work_id"],"title":item["title"],
            "slices":sorted(out,key=lambda s:s["slice_id"]),"recommended_parallel_waves":waves,
            "recommended_next_slice":ready[0]["slice_id"] if ready else None,
            "safety_boundaries":["candidate slices must come from authoritative roadmap/design evidence","engine never invents implementation tasks from prose","roadmap importance outranks leverage signals","leverage only breaks ties among otherwise eligible work","structural score is ordinal, never duration/ROI/probability/percent complete","active ownership blocks overlap","Knowledge Engine shared contracts require compatibility clearance","Product Reality dependencies fail closed","specialist-lane and merge gates remain authoritative"]}
