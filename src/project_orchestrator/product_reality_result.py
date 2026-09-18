from __future__ import annotations

from typing import Any

from .product_reality import validate_product_reality_debt


_AUTOMATABLE_KINDS = {"packaged_app", "unreal_playtest", "real_game_playback", "private_source_validation"}
_SUCCESS = {"PASS"}
_FAILURE = {"FAIL", "ENVIRONMENT_FAILURE"}
_NON_VERDICTS = {"REVIEW_REQUIRED", "PRODUCT_REALITY_REQUIRED"}


def reconcile_product_reality_result(entry: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    debt = validate_product_reality_debt(entry)
    if debt["kind"] not in _AUTOMATABLE_KINDS:
        raise ValueError("subjective Product Reality debt cannot be reconciled from worker evidence")
    if result.get("repository") != debt["repository"]:
        raise ValueError("worker evidence repository does not match Product Reality debt")
    if result.get("product_reality_authority") is True or result.get("merge_authority") is True:
        raise ValueError("worker evidence exceeds authority")
    status = result.get("status")
    evidence_ref = result.get("evidence_ref")
    if not isinstance(evidence_ref, str) or not evidence_ref.strip():
        raise ValueError("worker evidence requires a non-empty evidence_ref")
    if status in _SUCCESS:
        state = "VERIFIED"
    elif status in _FAILURE:
        state = "FAILED"
    elif status in _NON_VERDICTS:
        state = "PENDING"
    else:
        raise ValueError("unsupported worker evidence status")
    updated = dict(debt)
    updated["state"] = state
    updated.pop("debt_id", None)
    updated["source_ref"] = evidence_ref.strip()
    normalized = validate_product_reality_debt(updated)
    return {
        "previous_debt_id": debt["debt_id"],
        "debt": normalized,
        "worker_status": status,
        "failure_class": (str(result["failure_class"])[:100] if result.get("failure_class") else None),
        "learning_candidate": state == "FAILED" and bool(result.get("failure_class")),
    }
