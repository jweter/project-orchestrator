#!/usr/bin/env python3
"""Derive a bounded execution slice from an authoritative selected capability."""
from __future__ import annotations
import json
from typing import Any
from scripts.project_capability_graph import validate as validate_graph

def derive_slice(payload: dict[str, Any], *, repository: str, exact_head: str) -> dict[str, Any]:
    graph=validate_graph(payload)
    if graph["status"]=="COMPLETE":
        completion=payload.get("product_completion")
        evidence=completion.get("evidence") if isinstance(completion,dict) else None
        proof_head=completion.get("exact_head") if isinstance(completion,dict) else None
        checks=completion.get("checks") if isinstance(completion,dict) else None
        if (not isinstance(completion,dict) or completion.get("verified") is not True
                or proof_head!=exact_head or not isinstance(evidence,list)
                or not evidence or not all(isinstance(item,str) and item.strip() for item in evidence)
                or not isinstance(checks,list) or not checks
                or not all(isinstance(check,dict) and check.get("status")=="PASS"
                           and isinstance(check.get("name"),str) and check["name"].strip() for check in checks)):
            return {"schema_version":1,"status":"DIRECTION_REQUIRED","slice":None,
                    "issues":[{"code":"PRODUCT_COMPLETION_EVIDENCE_REQUIRED"}]}
        return {"schema_version":1,"status":"NO_WORK","slice":None,"issues":[]}
    if graph["status"]!="READY" or not graph.get("selected"):
        return {"schema_version":1,"status":graph["status"],"slice":None,"issues":graph["issues"]}
    cid=graph["selected"]
    capability=next((c for c in payload["capabilities"] if c.get("id")==cid),None)
    if not capability:
        return {"schema_version":1,"status":"DIRECTION_REQUIRED","slice":None,"issues":[{"code":"SELECTED_CAPABILITY_MISSING"}]}
    slices=capability.get("slices")
    if not isinstance(slices,list) or not slices:
        return {"schema_version":1,"status":"DIRECTION_REQUIRED","slice":None,"issues":[{"code":"EXECUTION_SLICE_REQUIRED","capability":cid}]}
    done={s.get("id") for s in slices if isinstance(s,dict) and s.get("state")=="VERIFIED_COMPLETE" and s.get("verification")}
    candidate=None
    for s in slices:
        if not isinstance(s,dict):continue
        sid=s.get("id")
        if not isinstance(sid,str) or not sid.strip():
            return {"schema_version":1,"status":"DIRECTION_REQUIRED","slice":None,"issues":[{"code":"INVALID_SLICE_ID","capability":cid}]}
        if not str(s.get("objective","")).strip() or not s.get("acceptance"):
            return {"schema_version":1,"status":"DIRECTION_REQUIRED","slice":None,"issues":[{"code":"SLICE_AUTHORITY_INCOMPLETE","capability":cid,"slice":sid}]}
        deps=s.get("depends_on",[])
        if not isinstance(deps,list):
            return {"schema_version":1,"status":"DIRECTION_REQUIRED","slice":None,"issues":[{"code":"INVALID_SLICE_DEPENDENCY","capability":cid,"slice":sid}]}
        if s.get("state")!="VERIFIED_COMPLETE" and all(d in done for d in deps) and candidate is None:
            candidate=s
    if candidate is None:
        return {"schema_version":1,"status":"DIRECTION_REQUIRED","slice":None,"issues":[{"code":"NO_EXECUTABLE_SLICE","capability":cid}]}
    return {"schema_version":1,"status":"READY","issues":[],"slice":{
        "capability_id":cid,
        "slice_id":candidate["id"],
        "repository":repository,
        "exact_head":exact_head,
        "objective":candidate["objective"],
        "acceptance":candidate["acceptance"],
        "allowed_paths":candidate.get("allowed_paths",[]),
        "forbidden_paths":candidate.get("forbidden_paths",[]),
    }}
