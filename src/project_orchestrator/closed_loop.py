from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

PROJECTS = ("knowledge-engine", "rocksmith", "everward")
LOOP_STAGES = ("SENSE", "REMEMBER", "DECIDE", "ACT", "VERIFY", "LEARN", "ADAPT")
ADVANCEMENTS = {
    "MERGED_GREEN_PR",
    "REPAIRED_EXISTING_WORK",
    "PREFLIGHT_GREEN",
    "READY_PR_OPENED",
    "SUBSTANTIAL_SLICE_ADVANCED",
    "NONE",
}


def validate_closed_loop_run(payload: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Validate one portfolio run as a complete evidence-backed learning loop.

    This validator never performs GitHub mutations. It proves whether the
    scheduler has current evidence and whether each product lane actually
    traversed the required stages or explicitly failed closed.
    """
    if not isinstance(payload, dict):
        raise ValueError("closed-loop run must be an object")
    if payload.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")

    observed_at = _parse_time(payload.get("observed_at"), "observed_at")
    current = now or datetime.now(timezone.utc)
    if observed_at > current:
        raise ValueError("observed_at cannot be in the future")
    age_seconds = (current - observed_at).total_seconds()
    max_age = payload.get("max_evidence_age_seconds", 3600)
    if isinstance(max_age, bool) or not isinstance(max_age, int) or max_age < 1:
        raise ValueError("max_evidence_age_seconds must be a positive integer")
    evidence_fresh = age_seconds <= max_age

    repos = payload.get("repositories_inspected")
    if not isinstance(repos, list) or len(repos) != 5 or len(set(repos)) != 5:
        raise ValueError("repositories_inspected must contain exactly five unique product repositories")

    projects = payload.get("projects")
    if not isinstance(projects, dict) or tuple(projects) != PROJECTS:
        raise ValueError("projects must contain the three independent product projects in canonical order")

    normalized_projects: dict[str, Any] = {}
    eligible = 0
    eligible_advanced = 0
    lessons_reused = 0
    lessons_recorded = 0
    closed_projects = 0

    for project in PROJECTS:
        item = projects[project]
        if not isinstance(item, dict):
            raise ValueError(f"{project} must be an object")

        eligibility = item.get("eligibility")
        if eligibility not in {"ELIGIBLE", "NO_SAFE_ELIGIBLE_WORK", "UNKNOWN"}:
            raise ValueError(f"{project}.eligibility is invalid")

        stages = item.get("stages")
        if not isinstance(stages, dict):
            raise ValueError(f"{project}.stages must be an object")

        stage_result: dict[str, Any] = {}
        for stage in LOOP_STAGES:
            evidence = stages.get(stage)
            if not isinstance(evidence, dict):
                raise ValueError(f"{project}.stages.{stage} must be an object")
            state = evidence.get("state")
            if state not in {"COMPLETE", "NOT_APPLICABLE", "BLOCKED", "UNKNOWN"}:
                raise ValueError(f"{project}.stages.{stage}.state is invalid")
            ref = evidence.get("evidence_ref")
            if state == "COMPLETE" and (not isinstance(ref, str) or not ref.strip()):
                raise ValueError(f"{project}.stages.{stage} COMPLETE requires evidence_ref")
            if state == "BLOCKED":
                reason = evidence.get("reason")
                if not isinstance(reason, str) or not reason.strip():
                    raise ValueError(f"{project}.stages.{stage} BLOCKED requires reason")
            stage_result[stage] = evidence

        if eligibility == "ELIGIBLE":
            eligible += 1
            for required_stage in ("SENSE", "REMEMBER", "DECIDE"):
                if stage_result[required_stage]["state"] != "COMPLETE":
                    raise ValueError(f"{project} ELIGIBLE requires {required_stage}=COMPLETE before execution")

        advancement = item.get("advancement", "NONE")
        if advancement not in ADVANCEMENTS:
            raise ValueError(f"{project}.advancement is invalid")

        if advancement != "NONE":
            if eligibility != "ELIGIBLE":
                raise ValueError(f"{project} advancement requires ELIGIBLE")
            if stage_result["ACT"]["state"] != "COMPLETE" or stage_result["VERIFY"]["state"] != "COMPLETE":
                raise ValueError(f"{project} advancement requires ACT and VERIFY evidence")
            eligible_advanced += 1

        if eligibility == "ELIGIBLE" and advancement == "NONE":
            reason = item.get("no_advancement_reason")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(f"{project} eligible without advancement requires no_advancement_reason")

        reused = item.get("prior_lessons_reused", [])
        recorded = item.get("lessons_recorded", [])
        if not isinstance(reused, list) or not all(isinstance(x, str) and x.strip() for x in reused):
            raise ValueError(f"{project}.prior_lessons_reused must be a string list")
        if not isinstance(recorded, list) or not all(isinstance(x, str) and x.strip() for x in recorded):
            raise ValueError(f"{project}.lessons_recorded must be a string list")
        lessons_reused += len(set(reused))
        lessons_recorded += len(set(recorded))

        # A run is closed for a product only if learning/adaptation were either
        # explicitly completed or proven not applicable. UNKNOWN is not closure.
        learning_closed = stage_result["LEARN"]["state"] in {"COMPLETE", "NOT_APPLICABLE"}
        adapt_closed = stage_result["ADAPT"]["state"] in {"COMPLETE", "NOT_APPLICABLE"}
        if learning_closed and adapt_closed:
            closed_projects += 1

        normalized_projects[project] = {
            **item,
            "stages": stage_result,
            "prior_lessons_reused": sorted(set(reused)),
            "lessons_recorded": sorted(set(recorded)),
        }

    advancement_rate = (
        {
            "status": "MEASURED",
            "numerator": eligible_advanced,
            "denominator": eligible,
            "value": eligible_advanced / eligible,
        }
        if eligible
        else {"status": "NOT_APPLICABLE", "reason": "no project proven ELIGIBLE"}
    )

    return {
        "schema_version": 1,
        "observed_at": observed_at.isoformat(),
        "evidence_age_seconds": age_seconds,
        "evidence_fresh": evidence_fresh,
        "max_evidence_age_seconds": max_age,
        "repositories_inspected": repos,
        "projects": normalized_projects,
        "kpis": {
            "eligible_projects": eligible,
            "eligible_projects_advanced": eligible_advanced,
            "product_advancement_rate": advancement_rate,
            "closed_learning_loops": closed_projects,
            "closed_learning_loop_target": 3,
            "prior_lessons_reused": lessons_reused,
            "lessons_recorded": lessons_recorded,
        },
        "trend_claim_authorized": evidence_fresh,
        "run_closed": evidence_fresh and closed_projects == 3,
        "rules": [
            "fresh evidence is required before trend or compounding claims",
            "ELIGIBLE projects require SENSE, REMEMBER, and DECIDE evidence before ACT",
            "advancement requires ACT and VERIFY evidence",
            "LEARN and ADAPT must close explicitly or be NOT_APPLICABLE",
            "UNKNOWN never counts as a completed learning-loop stage",
            "Product Reality remains lane-scoped and is never inferred",
        ],
    }


def _parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} is not valid ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)
