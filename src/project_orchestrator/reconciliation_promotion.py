from __future__ import annotations

from typing import Any

from .branch_reconciliation import build_reconciliation_plan
from .flow_signal import build_stale_promotion_recovery, classify_workflow_outcome


def reconcile_packet(packet: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
    """Bind an Exponential Engine packet to existing reconciliation authority."""
    required = {"packet_id", "repository", "branch", "base_sha", "exact_head_sha", "state", "changed_paths"}
    missing = sorted(required - packet.keys())
    if missing:
        return {"state": "BLOCKED", "reason": "INCOMPLETE_PACKET", "missing": missing, "merge_allowed": False}
    if packet["state"] != "PREFLIGHT_GREEN":
        return {"state": "BLOCKED", "reason": "EXACT_HEAD_PREFLIGHT_NOT_GREEN", "merge_allowed": False}
    if live.get("repository") != packet["repository"] or live.get("branch") != packet["branch"]:
        return {"state": "BLOCKED", "reason": "LIVE_IDENTITY_MISMATCH", "merge_allowed": False}
    if live.get("contract_uncertain") is True or live.get("overlap_detected") is True:
        return {"state": "RECONCILE_REQUIRED", "reason": "OVERLAP_OR_CONTRACT_UNCERTAINTY", "merge_allowed": False}

    evidence = {
        "repository": packet["repository"],
        "default_branch": live.get("default_branch", "main"),
        "default_head_sha": live.get("default_head_sha"),
        "branch": packet["branch"],
        "branch_head_sha": live.get("branch_head_sha"),
        "ahead_by": live.get("ahead_by"),
        "behind_by": live.get("behind_by"),
        "changed_files": live.get("changed_files", packet["changed_paths"]),
        "open_pr": live.get("open_pr"),
        "actively_owned": live.get("actively_owned", False),
        "roadmap_authorized": live.get("roadmap_authorized"),
        "product_reality_dependency": live.get("product_reality_dependency", False),
        "product_reality_verified": live.get("product_reality_verified", False),
    }
    try:
        plan = build_reconciliation_plan(evidence)
    except (TypeError, ValueError) as exc:
        return {"state": "BLOCKED", "reason": "INVALID_RECONCILIATION_EVIDENCE", "detail": str(exc), "merge_allowed": False}

    if plan["blockers"]:
        return {"state": "BLOCKED", "reason": "RECONCILIATION_BLOCKED", "plan": plan, "merge_allowed": False}
    if plan["modernization_ready"]:
        return {"state": "RECONCILE_REQUIRED", "reason": "DEFAULT_HEAD_DIVERGED", "plan": plan, "merge_allowed": False}
    if not plan["promotable"]:
        return {"state": "BLOCKED", "reason": "BRANCH_NOT_PROMOTABLE", "plan": plan, "merge_allowed": False}
    if live.get("branch_head_sha") != packet["exact_head_sha"]:
        return {"state": "RECONCILE_REQUIRED", "reason": "WORKER_HEAD_MOVED", "plan": plan, "merge_allowed": False}
    return {"state": "PROMOTION_READY", "reason": "RECONCILED_EXACT_HEAD_GREEN", "plan": plan, "exact_head_sha": packet["exact_head_sha"], "merge_allowed": False}


def evaluate_promotion(packet: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    """Evaluate fresh CI/review/mergeability without granting merge authority."""
    if packet.get("state") != "PROMOTION_READY":
        return {"state": "BLOCKED", "reason": "RECONCILIATION_NOT_COMPLETE", "merge_allowed": False}
    expected = packet.get("exact_head_sha")
    actual = evidence.get("head_sha")
    if expected != actual:
        recovery = build_stale_promotion_recovery({
            "repository": packet.get("repository"),
            "branch": packet.get("branch"),
            "expected_head_sha": expected,
            "current_head_sha": actual,
            "verified_head_sha": evidence.get("verified_head_sha"),
        })
        return {"state": "RECONCILE_REQUIRED", "reason": "PROMOTION_HEAD_MOVED", "recovery": recovery, "merge_allowed": False}

    workflow = classify_workflow_outcome({
        "applicable": True,
        "expected_head_sha": expected,
        "actual_head_sha": actual,
        "workflow_status": evidence.get("ci_status"),
        "workflow_conclusion": evidence.get("ci_conclusion"),
        "failure_class": evidence.get("failure_class"),
        "failed_stage": evidence.get("failed_stage"),
        "failure_text": evidence.get("failure_text"),
    })
    if workflow["operational_state"] != "VERIFIED_GREEN":
        return {"state": "CI_PENDING" if workflow["operational_state"] == "PENDING" else "BLOCKED", "reason": workflow["operational_state"], "workflow": workflow, "merge_allowed": False}
    if evidence.get("mergeable") is not True:
        return {"state": "BLOCKED", "reason": "MERGEABILITY_NOT_GREEN", "merge_allowed": False}
    if evidence.get("required_reviews_satisfied") is not True:
        return {"state": "BLOCKED", "reason": "REQUIRED_REVIEWS_NOT_SATISFIED", "merge_allowed": False}
    if evidence.get("unresolved_review_threads", 0) != 0:
        return {"state": "BLOCKED", "reason": "UNRESOLVED_REVIEW_THREADS", "merge_allowed": False}
    if evidence.get("lease_valid") is not True:
        return {"state": "BLOCKED", "reason": "OWNERSHIP_LEASE_NOT_VALID", "merge_allowed": False}
    if evidence.get("policy_clear") is not True:
        return {"state": "BLOCKED", "reason": "POLICY_GATE_NOT_CLEAR", "merge_allowed": False}
    return {
        "state": "CENTRAL_MERGE_REVALIDATION_READY",
        "reason": "FRESH_CI_REVIEWS_MERGEABILITY_AND_POLICY_GREEN",
        "exact_head_sha": expected,
        "merge_allowed": False,
        "next_event": "central_merge_revalidation",
    }
