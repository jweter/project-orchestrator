from __future__ import annotations

from typing import Any

from .jev_evidence import attach_outcome, decision_record

VERIFIED_STATES = {"MERGED", "PRODUCT_REALITY_VERIFIED", "VERIFIED_ADVANCEMENT"}
FAILED_STATES = {"FAIL", "FAILED", "CI_FAILED", "PREFLIGHT_FAILED", "QUALITY_FAILURE", "CODE_FAILURE", "TEST_FAILURE"}
ABANDONED_STATES = {"ABANDONED", "CANCELLED", "REJECTED"}


def records_from_foreman_plan(plan: dict[str, Any], *, run_id: str) -> list[dict[str, Any]]:
    projects = {row["project_id"]: row for row in plan.get("projects", []) if isinstance(row, dict)}
    out = []
    for project_id, shadow in plan.get("jev_shadow", {}).items():
        if not isinstance(shadow, dict):
            continue
        legal = shadow.get("deterministic_legal_candidate_ids", [])
        if len(legal) < 2:
            continue
        row = projects.get(project_id, {})
        selected = row.get("selected_lane")
        deterministic = selected.get("lane_id") if isinstance(selected, dict) else None
        out.append(decision_record(
            run_id=run_id,
            project_id=project_id,
            deterministic_choice=deterministic,
            shadow=shadow,
        ))
    harvest = plan.get("jev_decision_harvest", {})
    for key, shadow in harvest.items():
        if not isinstance(shadow, dict):
            continue
        legal = shadow.get("deterministic_legal_candidate_ids", [])
        if len(legal) < 2:
            continue
        decision_class = key.split(":", 1)[1] if ":" in key else key
        project_id = key.split(":", 1)[0]
        deterministic = legal[0] if legal else None
        out.append(decision_record(
            run_id=run_id, project_id=project_id,
            deterministic_choice=deterministic, shadow=shadow,
            decision_class=decision_class,
            decision_id=f"{run_id}:{key}",
        ))
    return out


def _result_state(result: dict[str, Any]) -> str:
    return str(
        result.get("state")
        or result.get("status")
        or result.get("result_status")
        or result.get("continuation")
        or ""
    ).upper()


def reconcile_record(record: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    if record.get("outcome") is not None:
        return record
    candidate = result.get("lane_id") or result.get("candidate_id")
    if candidate not in record.get("legal_candidate_ids", []):
        return record
    state = _result_state(result)
    verification_ref = result.get("verification_ref") or result.get("head_sha")
    if state in VERIFIED_STATES:
        outcome = "VERIFIED_ADVANCEMENT"
    elif state in FAILED_STATES:
        outcome = "FAILED"
    elif state in ABANDONED_STATES:
        outcome = "ABANDONED"
    else:
        return record
    return attach_outcome(
        record,
        candidate_id=str(candidate),
        outcome=outcome,
        verification_ref=str(verification_ref) if verification_ref else None,
        retries=int(result.get("retries", 0) or 0),
        verification_churn=int(result.get("verification_churn", 0) or 0),
    )


def reconcile_records(records: list[dict[str, Any]], results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    updated = list(records)
    for index, record in enumerate(updated):
        for result in results:
            if not isinstance(result, dict):
                continue
            candidate = result.get("lane_id") or result.get("candidate_id")
            if candidate not in record.get("legal_candidate_ids", []):
                continue
            updated[index] = reconcile_record(record, result)
            if updated[index].get("outcome") is not None:
                break
    return updated
