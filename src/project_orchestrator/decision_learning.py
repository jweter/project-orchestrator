from __future__ import annotations

from collections import defaultdict
from typing import Any


def _verified(row: dict[str, Any]) -> bool:
    return row.get("state") == "VERIFIED_ADVANCEMENT" and bool(row.get("verification_ref"))


def compile_decision_learning(attributions: list[dict[str, Any]], *,
                              minimum_verified_samples: int = 3) -> dict[str, Any]:
    """Turn verified decision outcomes into bounded advisory planning evidence."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in attributions:
        if isinstance(row, dict) and row.get("decision_class"):
            grouped[str(row["decision_class"])].append(row)

    policies = []
    for decision_class, rows in sorted(grouped.items()):
        verified = [row for row in rows if _verified(row)]
        if len(verified) < minimum_verified_samples:
            policies.append({
                "decision_class": decision_class,
                "state": "INSUFFICIENT_VERIFIED_EVIDENCE",
                "verified_samples": len(verified),
                "minimum_verified_samples": minimum_verified_samples,
                "preferred_selector": None,
            })
            continue
        shadow = sum(1 for row in verified if row.get("shadow_selected"))
        deterministic = sum(1 for row in verified if row.get("deterministic_selected"))
        preferred = None
        if shadow > deterministic:
            preferred = "JEV_SHADOW"
        elif deterministic > shadow:
            preferred = "DETERMINISTIC_FOREMAN"
        policies.append({
            "decision_class": decision_class,
            "state": "VERIFIED_ADVISORY_EVIDENCE",
            "verified_samples": len(verified),
            "shadow_verified": shadow,
            "deterministic_verified": deterministic,
            "preferred_selector": preferred,
            "mean_retries": sum(int(row.get("retries", 0)) for row in verified) / len(verified),
            "mean_verification_churn": sum(int(row.get("verification_churn", 0)) for row in verified) / len(verified),
            "mean_elapsed_seconds": sum(float(row.get("elapsed_seconds", 0)) for row in verified) / len(verified),
        })
    return {
        "schema_version": 1,
        "mode": "VERIFIED_DECISION_LEARNING",
        "policies": policies,
        "authority": "ADVISORY_ONLY",
        "automatic_promotion": False,
        "product_reality_may_be_inferred": False,
    }


def planning_hint(learning: dict[str, Any], decision_class: str) -> dict[str, Any]:
    """Return verified history to planning without changing legal candidates or authority."""
    for policy in learning.get("policies", []):
        if policy.get("decision_class") == decision_class:
            return {
                "decision_class": decision_class,
                "evidence_state": policy.get("state"),
                "preferred_selector": policy.get("preferred_selector"),
                "verified_samples": policy.get("verified_samples", 0),
                "authority": "ADVISORY_ONLY",
                "may_change_legal_candidate_set": False,
                "may_bypass_gates": False,
            }
    return {
        "decision_class": decision_class, "evidence_state": "NO_EVIDENCE",
        "preferred_selector": None, "verified_samples": 0,
        "authority": "ADVISORY_ONLY", "may_change_legal_candidate_set": False,
        "may_bypass_gates": False,
    }
