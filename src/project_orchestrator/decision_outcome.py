from __future__ import annotations

from collections import defaultdict
from typing import Any

VERIFIED = {"MERGED", "PRODUCT_REALITY_VERIFIED", "VERIFIED_ADVANCEMENT"}
FAILED = {"FAIL", "FAILED", "CI_FAILED", "PREFLIGHT_FAILED", "QUALITY_FAILURE", "CODE_FAILURE", "TEST_FAILURE"}
ABANDONED = {"ABANDONED", "CANCELLED", "REJECTED"}


def decision_id(run_id: str, decision_class: str, project_id: str) -> str:
    return f"{run_id}:{decision_class}:{project_id}"


def classify_result(result: dict[str, Any]) -> str | None:
    state = str(result.get("state") or result.get("status") or result.get("result_status") or "").upper()
    if state in VERIFIED:
        return "VERIFIED_ADVANCEMENT"
    if state in FAILED:
        return "FAILED"
    if state in ABANDONED:
        return "ABANDONED"
    return None


def outcome_metrics(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "elapsed_seconds": max(0.0, float(result.get("elapsed_seconds", 0) or 0)),
        "retries": max(0, int(result.get("retries", 0) or 0)),
        "verification_churn": max(0, int(result.get("verification_churn", 0) or 0)),
        "failure_class": result.get("failure_class"),
        "verification_ref": result.get("verification_ref") or result.get("head_sha"),
        "product_reality": result.get("product_reality_state"),
    }


def attribute(record: dict[str, Any], result: dict[str, Any]) -> dict[str, Any] | None:
    candidate = result.get("lane_id") or result.get("candidate_id")
    if candidate not in record.get("legal_candidate_ids", []):
        return None
    state = classify_result(result)
    if state is None:
        return None
    deterministic = record.get("deterministic_choice")
    shadow = record.get("shadow_choice")
    return {
        "decision_id": record.get("decision_id"),
        "decision_class": record.get("decision_class", "candidate-ordering"),
        "project_id": record.get("project_id"),
        "candidate_id": candidate,
        "state": state,
        "deterministic_selected": candidate == deterministic,
        "shadow_selected": candidate == shadow,
        "disagreed": bool(deterministic and shadow and deterministic != shadow),
        **outcome_metrics(result),
    }


def summarize(attributions: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in attributions:
        groups[str(row.get("decision_class", "unknown"))].append(row)
    by_class = {}
    for name, rows in sorted(groups.items()):
        verified = [r for r in rows if r["state"] == "VERIFIED_ADVANCEMENT"]
        disagreements = [r for r in rows if r.get("disagreed")]
        by_class[name] = {
            "outcomes": len(rows),
            "verified_advancements": len(verified),
            "disagreements": len(disagreements),
            "mean_retries": sum(r["retries"] for r in rows) / len(rows),
            "mean_verification_churn": sum(r["verification_churn"] for r in rows) / len(rows),
            "mean_elapsed_seconds": sum(r["elapsed_seconds"] for r in rows) / len(rows),
            "shadow_verified": sum(1 for r in verified if r.get("shadow_selected")),
            "deterministic_verified": sum(1 for r in verified if r.get("deterministic_selected")),
        }
    return {
        "mode": "DESCRIPTIVE_OUTCOME_EVIDENCE",
        "outcomes_total": len(attributions),
        "by_decision_class": by_class,
        "promotion_authority": False,
        "policy_mutation_authority": False,
    }
