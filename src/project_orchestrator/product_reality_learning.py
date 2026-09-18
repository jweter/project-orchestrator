from __future__ import annotations

from typing import Any

from .failure_learning import persist_verified_failure
from .product_reality_result import reconcile_product_reality_result


_REQUIRED_DIAGNOSIS = {
    "root_cause",
    "fix",
    "verification",
    "regression_protection",
    "residual_risk",
    "log_text",
}


def reconcile_and_learn(
    memory: dict[str, Any],
    entry: dict[str, Any],
    result: dict[str, Any],
    *,
    diagnosis: dict[str, str] | None = None,
) -> dict[str, Any]:
    reconciled = reconcile_product_reality_result(entry, result)
    if not reconciled["learning_candidate"]:
        return {"reconciliation": reconciled, "memory": memory, "learning_status": "NOT_APPLICABLE"}
    if diagnosis is None:
        return {"reconciliation": reconciled, "memory": memory, "learning_status": "DIAGNOSIS_REQUIRED"}
    missing = sorted(key for key in _REQUIRED_DIAGNOSIS if not isinstance(diagnosis.get(key), str) or not diagnosis[key].strip())
    if missing:
        raise ValueError("verified failure diagnosis missing fields: " + ", ".join(missing))
    updated_memory = persist_verified_failure(
        memory,
        repository=entry["repository"],
        source_ref=result["evidence_ref"],
        log_text=diagnosis["log_text"],
        root_cause=diagnosis["root_cause"],
        fix=diagnosis["fix"],
        verification=diagnosis["verification"],
        regression_protection=diagnosis["regression_protection"],
        residual_risk=diagnosis["residual_risk"],
    )
    return {"reconciliation": reconciled, "memory": updated_memory, "learning_status": "VERIFIED_LESSON_PERSISTED"}
