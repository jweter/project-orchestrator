#!/usr/bin/env python3
"""Build product-completion proof only from exact-head authoritative evidence."""
from __future__ import annotations
from typing import Any

def build_completion_proof(*, exact_head:str, acceptance:list[dict[str,Any]], product_reality:list[dict[str,Any]])->dict[str,Any]:
    if not isinstance(exact_head,str) or len(exact_head)!=40:
        return {"status":"DIRECTION_REQUIRED","issues":[{"code":"EXACT_HEAD_REQUIRED"}]}
    checks=[]; evidence=[]
    if not isinstance(acceptance,list) or not acceptance:
        return {"status":"DIRECTION_REQUIRED","issues":[{"code":"FINAL_ACCEPTANCE_EVIDENCE_REQUIRED"}]}
    for item in acceptance:
        if not isinstance(item,dict) or item.get("exact_head")!=exact_head or item.get("status")!="PASS" or not item.get("evidence_ref"):
            return {"status":"DIRECTION_REQUIRED","issues":[{"code":"FINAL_ACCEPTANCE_NOT_PROVEN"}]}
        checks.append({"name":str(item.get("name") or "acceptance"),"status":"PASS"})
        evidence.append(str(item["evidence_ref"]))
    if not isinstance(product_reality,list):
        return {"status":"DIRECTION_REQUIRED","issues":[{"code":"PRODUCT_REALITY_EVIDENCE_REQUIRED"}]}
    for item in product_reality:
        if not isinstance(item,dict) or item.get("exact_head")!=exact_head or item.get("state")!="VERIFIED" or not item.get("source_ref"):
            return {"status":"DIRECTION_REQUIRED","issues":[{"code":"PRODUCT_REALITY_NOT_VERIFIED"}]}
        checks.append({"name":"product-reality:"+str(item.get("kind") or "evidence"),"status":"PASS"})
        evidence.append(str(item["source_ref"]))
    return {"status":"VERIFIED","product_completion":{"verified":True,"exact_head":exact_head,
        "evidence":sorted(set(evidence)),"checks":checks}}
