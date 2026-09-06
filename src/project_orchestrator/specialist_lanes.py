from __future__ import annotations

import hashlib
import json
from typing import Any

RESULT_FIELDS = {
    "lane_id","project_id","repository","base_sha","head_sha","changed_files",
    "preflight_state","dependency_impact","product_reality_debt","continuation",
}


def _id(data: dict[str, Any]) -> str:
    raw=json.dumps(data,sort_keys=True,separators=(",",":")).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _repo_projects(inventory: dict[str, Any]) -> dict[str,str]:
    return {
        repo["name"]: project["id"]
        for project in inventory.get("projects",[])
        for repo in project.get("repositories",[])
    }


def _overlap(left: list[str], right: list[str]) -> bool:
    for a in left:
        for b in right:
            if a==b or a.startswith(b.rstrip("/")+"/") or b.startswith(a.rstrip("/")+"/"):
                return True
    return False


def build_lane_plan(
    inventory: dict[str, Any],
    live_snapshot: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Reserve conflict-free specialist work without delegating merge authority."""
    repo_projects=_repo_projects(inventory)
    repos={
        item["repository"]: item
        for item in live_snapshot.get("repositories",[])
        if isinstance(item.get("repository"),str)
    }
    claimed_projects:set[str]=set()
    lanes:list[dict[str,Any]]=[]

    for candidate in sorted(candidates,key=lambda x:(x.get("priority",999),x.get("project_id",""),x.get("repository",""),x.get("title",""))):
        project=candidate.get("project_id")
        repository=candidate.get("repository")
        live=repos.get(repository,{})
        state="RESERVED"
        reason="conflict-free specialist reservation"

        if repo_projects.get(repository)!=project:
            state,reason="BLOCKED","candidate does not match canonical portfolio inventory"
        elif candidate.get("substantial",True) and project in claimed_projects:
            state,reason="BLOCKED","one substantial coordinated slice per independent project per run"
        elif live.get("default_head_sha") and candidate.get("base_sha")!=live.get("default_head_sha"):
            state,reason="STALE","candidate base differs from live default head"
        elif candidate.get("requires_product_reality_result") and not candidate.get("product_reality_verified"):
            state,reason="BLOCKED","candidate depends on unverified Product Reality"
        elif project=="knowledge-engine" and candidate.get("shared_contract_change") and not candidate.get("compatibility_plan_clear"):
            state,reason="BLOCKED","Knowledge Engine shared-contract change lacks compatibility clearance"
        else:
            for active in live.get("active_work",[]):
                same_branch=candidate.get("branch") and candidate.get("branch")==active.get("branch")
                same_issue=candidate.get("issue") is not None and candidate.get("issue")==active.get("issue")
                if same_branch:
                    continue
                if same_issue or _overlap(candidate.get("paths",[]),active.get("changed_files",[])):
                    state,reason="BLOCKED","candidate overlaps active GitHub ownership"
                    break

        if state=="RESERVED" and candidate.get("substantial",True):
            claimed_projects.add(project)

        identity={
            "project_id":project,"repository":repository,"branch":candidate.get("branch"),
            "issue":candidate.get("issue"),"title":candidate.get("title"),
            "base_sha":candidate.get("base_sha"),
        }
        lanes.append({
            "lane_id":_id(identity),**identity,"state":state,"reason":reason,
            "priority":candidate.get("priority"),"paths":sorted(set(candidate.get("paths",[]))),
            "dependencies":sorted(set(candidate.get("dependencies",[]))),
            "parent_lane":"knowledge-engine" if project=="knowledge-engine" else project,
            "merge_authority":False,
            "may_prepare_branch":state=="RESERVED",
            "may_open_pr_only_after_preflight_green":state=="RESERVED",
        })

    return {
        "schema_version":1,
        "merge_authority":"portfolio-orchestrator-only",
        "lanes":lanes,
        "safety_boundaries":[
            "reservations are ephemeral projections of live GitHub ownership",
            "one substantial coordinated slice per independent project per run",
            "Knowledge Engine shared contracts require compatibility clearance",
            "specialist lanes never merge",
            "changed base heads make lanes stale",
            "Product Reality is never inferred",
        ],
    }


def validate_lane_result(result: dict[str,Any], *, live_default_head: str|None=None) -> dict[str,Any]:
    missing=sorted(RESULT_FIELDS-result.keys())
    if missing:
        return {"valid":False,"state":"BLOCKED","reason":"missing fields: "+", ".join(missing)}
    if not isinstance(result["changed_files"],list) or not all(isinstance(x,str) for x in result["changed_files"]):
        return {"valid":False,"state":"BLOCKED","reason":"changed_files must be a path list"}
    if live_default_head is not None and result["base_sha"]!=live_default_head:
        return {"valid":True,"state":"STALE","reason":"base head changed; lane must be revalidated"}
    if result["preflight_state"]!="PREFLIGHT_GREEN":
        return {"valid":True,"state":"PREFLIGHT_PENDING","reason":"normal development PR is not yet permitted"}
    return {
        "valid":True,"state":"PREFLIGHT_GREEN",
        "reason":"central orchestrator must still reverify live state, open PR, and require fresh independent CI",
    }
