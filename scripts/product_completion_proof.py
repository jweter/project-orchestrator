#!/usr/bin/env python3
"""Build product-completion proof only from exact-head authoritative evidence."""
from __future__ import annotations
from typing import Any

def build_completion_proof(*, exact_head:str, criteria:list[dict[str,Any]]|None=None, evidence:list[dict[str,Any]]|None=None,
                           acceptance:list[dict[str,Any]]|None=None, product_reality:list[dict[str,Any]]|None=None)->dict[str,Any]:
    # Compatibility adapter: legacy final evidence is normalized into the same
    # criterion-bound proof contract. New callers should pass criteria/evidence.
    if criteria is None and evidence is None and (acceptance is not None or product_reality is not None):
        acceptance=acceptance if isinstance(acceptance,list) else []
        product_reality=product_reality if isinstance(product_reality,list) else []
        criteria=[{"id":"final-acceptance","verification_kind":"acceptance"}]
        evidence=[{"criterion_id":"final-acceptance","verification_kind":"acceptance",
                   "exact_head":item.get("exact_head"),"status":item.get("status"),
                   "evidence_ref":item.get("evidence_ref")} for item in acceptance if isinstance(item,dict)]
        if product_reality:
            criteria.append({"id":"product-reality","verification_kind":"product_reality"})
            evidence.extend({"criterion_id":"product-reality","verification_kind":"product_reality",
                             "exact_head":item.get("exact_head"),
                             "status":"PASS" if item.get("state")=="VERIFIED" else item.get("state"),
                             "evidence_ref":item.get("source_ref")} for item in product_reality if isinstance(item,dict))
    criteria=criteria if isinstance(criteria,list) else []
    evidence=evidence if isinstance(evidence,list) else []
    if not isinstance(exact_head,str) or len(exact_head)!=40:
        return {"status":"DIRECTION_REQUIRED","issues":[{"code":"EXACT_HEAD_REQUIRED"}]}
    if not isinstance(criteria,list) or not criteria:
        return {"status":"DIRECTION_REQUIRED","issues":[{"code":"COMPLETION_CRITERIA_REQUIRED"}]}
    criterion_ids=[str(item.get("id") or "") for item in criteria if isinstance(item,dict)]
    if len(criterion_ids)!=len(criteria) or not all(criterion_ids) or len(set(criterion_ids))!=len(criterion_ids):
        return {"status":"DIRECTION_REQUIRED","issues":[{"code":"INVALID_COMPLETION_CRITERIA"}]}
    by_id={}
    for item in evidence if isinstance(evidence,list) else []:
        if isinstance(item,dict) and item.get("criterion_id"):
            by_id.setdefault(str(item["criterion_id"]),[]).append(item)
    checks=[]; refs=[]
    for criterion in criteria:
        cid=str(criterion["id"]); kind=str(criterion.get("verification_kind") or "")
        matches=by_id.get(cid,[])
        valid=[item for item in matches if item.get("exact_head")==exact_head
               and item.get("verification_kind")==kind and item.get("status")=="PASS"
               and item.get("evidence_ref")]
        if not valid:
            return {"status":"DIRECTION_REQUIRED","issues":[{"code":"COMPLETION_CRITERION_NOT_PROVEN","criterion_id":cid,"verification_kind":kind}]}
        checks.append({"name":cid,"verification_kind":kind,"status":"PASS"})
        refs.extend(str(item["evidence_ref"]) for item in valid)
    undeclared=sorted(set(by_id)-set(criterion_ids))
    if undeclared:
        return {"status":"DIRECTION_REQUIRED","issues":[{"code":"UNDECLARED_COMPLETION_EVIDENCE","criteria":undeclared}]}
    return {"status":"VERIFIED","product_completion":{"verified":True,"exact_head":exact_head,
        "evidence":sorted(set(refs)),"checks":checks}}
