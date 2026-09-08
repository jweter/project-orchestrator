from __future__ import annotations

from typing import Any

from .branch_reconciliation import build_reconciliation_plan

_REQUIRED = {
    "repository", "default_branch", "default_head_sha", "branch", "branch_head_sha",
    "ahead_by", "behind_by", "changed_files",
}


def classify_branch_lifecycle(evidence: dict[str, Any]) -> dict[str, Any]:
    """Classify a non-default branch and emit only evidence-safe lifecycle actions."""
    missing = _REQUIRED - evidence.keys()
    if missing:
        raise ValueError("branch lifecycle evidence missing fields: " + ", ".join(sorted(missing)))

    plan = build_reconciliation_plan(evidence)
    open_pr = evidence.get("open_pr")
    merged_pr = evidence.get("merged_pr")
    actively_owned = bool(evidence.get("actively_owned", False))
    release_required = bool(evidence.get("release_or_hotfix_required", False))
    preservation_reason = evidence.get("preservation_reason")
    unique_unmerged_work = plan["ahead_by"] > 0
    disposition_certain = evidence.get("disposition_certain") is True

    reasons: list[str] = []
    safe_to_delete = False
    queue_for_modernization = False

    if open_pr:
        state = "PR_OWNED"
        reasons.append("open PR owns the branch")
    elif actively_owned:
        state = "ACTIVE"
        reasons.append("active ownership is present")
    elif release_required:
        state = "RELEASE_OR_HOTFIX_REQUIRED"
        reasons.append("branch is explicitly required for release or hotfix work")
    elif preservation_reason:
        state = "PRESERVED"
        reasons.append(str(preservation_reason))
    elif merged_pr and not unique_unmerged_work and disposition_certain:
        state = "MERGED_DISPOSABLE"
        safe_to_delete = True
        reasons.append("merged PR verified and no unique unmerged work remains")
    elif plan["state"] == "DIVERGED_VALUABLE":
        state = "STALE_VALUABLE"
        queue_for_modernization = plan["modernization_ready"]
        reasons.extend(plan["blockers"] or ["unique authorized work diverges from current default"])
    elif plan["state"] == "CURRENT_UNMERGED":
        state = "ACTIVE_UNMERGED"
        reasons.extend(plan["blockers"] or ["unique work is current with default and requires verification/promotion"])
    elif plan["state"] in {"STALE_NO_UNIQUE_WORK", "ALIGNED_NO_UNIQUE_WORK"}:
        if disposition_certain:
            state = "SUPERSEDED_OR_DISPOSABLE"
            safe_to_delete = True
            reasons.append("no unique work remains and branch disposition is proven")
        else:
            state = "UNCERTAIN"
            reasons.append("no unique work detected but branch disposition has not been proven")
    else:
        state = "UNCERTAIN"
        reasons.append("available evidence is insufficient for a destructive lifecycle action")

    return {
        "schema_version": 1,
        "repository": plan["repository"],
        "branch": plan["branch"],
        "branch_head_sha": plan["branch_head_sha"],
        "default_head_sha": plan["default_head_sha"],
        "lifecycle_state": state,
        "safe_to_delete": safe_to_delete,
        "queue_for_modernization": queue_for_modernization,
        "reasons": reasons,
        "reconciliation": plan,
        "next_action": (
            "DELETE_BRANCH"
            if safe_to_delete
            else "QUEUE_MODERNIZATION"
            if queue_for_modernization
            else "CONTINUE_EXISTING_LANE"
            if state in {"PR_OWNED", "ACTIVE", "ACTIVE_UNMERGED"}
            else "PRESERVE"
        ),
        "destructive_guard": "branch deletion requires explicit safe_to_delete=true from complete live evidence; classification alone never deletes refs",
    }


def classify_portfolio_branches(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(items, list):
        raise ValueError("branch lifecycle input must be a list")
    classified = [classify_branch_lifecycle(item) for item in items]
    return {
        "schema_version": 1,
        "branches": classified,
        "counts": {
            state: sum(1 for item in classified if item["lifecycle_state"] == state)
            for state in sorted({item["lifecycle_state"] for item in classified})
        },
        "deletion_candidates": [
            {"repository": item["repository"], "branch": item["branch"], "head_sha": item["branch_head_sha"]}
            for item in classified if item["safe_to_delete"]
        ],
        "modernization_queue": [
            {"repository": item["repository"], "branch": item["branch"], "head_sha": item["branch_head_sha"]}
            for item in classified if item["queue_for_modernization"]
        ],
    }
