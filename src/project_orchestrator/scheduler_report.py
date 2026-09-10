from __future__ import annotations

from typing import Any

from .run_report import PROJECT_KEYS, build_run_report

ADVANCEMENTS = {
    "MERGED_GREEN_PR",
    "REPAIRED_EXISTING_WORK",
    "PREFLIGHT_GREEN",
    "READY_PR_OPENED",
    "SUBSTANTIAL_SLICE_ADVANCED",
}

ADVANCEMENT_ACTORS = {
    "ORCHESTRATOR",
    "JEREMY",
    "EXTERNAL_AUTOMATION",
    "OTHER",
    "UNKNOWN",
}


def build_scheduler_executive_report(
    *,
    project_evidence: dict[str, dict[str, Any]],
    results: dict[str, list[str]],
    project_orchestrator: dict[str, Any],
    product_repositories_inspected: int,
    learning_evolution: dict[str, Any] | None = None,
    learning_effectiveness: dict[str, Any] | None = None,
    jeremy_can_help: list[str] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build the scheduler-facing executive report from explicit run evidence.

    Only work actually performed by the current orchestrator run may satisfy
    product advancement or the throughput floor. Human merges, external CI
    completions, and other outside state changes remain visible but cannot be
    claimed as this run's engineering output.
    """
    if isinstance(product_repositories_inspected, bool) or not isinstance(product_repositories_inspected, int):
        raise ValueError("product_repositories_inspected must be an integer")
    if not 0 <= product_repositories_inspected <= 5:
        raise ValueError("product_repositories_inspected must be between 0 and 5")

    canonical_projects: dict[str, dict[str, Any]] = {}
    eligible: list[str] = []
    orchestrator_advanced: list[str] = []
    external_state_changes: dict[str, dict[str, str]] = {}
    no_advancement: dict[str, str] = {}

    for project in PROJECT_KEYS:
        if project not in project_evidence:
            raise ValueError(f"missing project evidence: {project}")
        evidence = project_evidence[project]
        eligibility = evidence.get("eligibility")
        if eligibility not in {"ELIGIBLE", "NO_SAFE_ELIGIBLE_WORK", "UNKNOWN"}:
            raise ValueError(f"{project}.eligibility is invalid")

        advancement = evidence.get("advancement", "NONE")
        if advancement not in ADVANCEMENTS | {"NONE"}:
            raise ValueError(f"{project}.advancement is invalid")
        if eligibility == "NO_SAFE_ELIGIBLE_WORK" and advancement != "NONE":
            raise ValueError(f"{project} cannot advance while NO_SAFE_ELIGIBLE_WORK")

        actor = evidence.get("advancement_actor", "UNKNOWN" if advancement != "NONE" else None)
        if advancement != "NONE":
            if actor not in ADVANCEMENT_ACTORS:
                raise ValueError(f"{project}.advancement_actor is invalid")
            if actor == "UNKNOWN":
                raise ValueError(
                    f"{project}.advancement_actor must be known when advancement is claimed"
                )

        if eligibility == "ELIGIBLE":
            eligible.append(project)

        if advancement != "NONE" and actor == "ORCHESTRATOR":
            orchestrator_advanced.append(project)
        elif advancement != "NONE":
            external_state_changes[project] = {
                "advancement": advancement,
                "actor": actor,
                "note": evidence.get("advancement_note", "external state change observed"),
            }

        if eligibility == "ELIGIBLE" and project not in orchestrator_advanced:
            reason = evidence.get("no_advancement_reason")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(
                    f"{project} requires no_advancement_reason when eligible but not advanced by this orchestrator run"
                )
            no_advancement[project] = reason.strip()

        canonical_projects[project] = {
            "promotion_state": evidence.get("promotion_state", "PREFLIGHT_UNVERIFIED"),
            "implementation_verification": evidence.get("implementation_verification", "UNKNOWN"),
            "active_ownership": evidence.get("active_ownership", "UNKNOWN"),
            "next_gate": evidence.get("next_gate", "UNKNOWN"),
            "reason": evidence.get("reason"),
            "product_reality_debt": evidence.get("product_reality_debt", []),
        }

    canonical = build_run_report(
        projects=canonical_projects,
        results=results,
        project_orchestrator=project_orchestrator,
        generated_at=generated_at,
    )

    eligible_advanced = [project for project in orchestrator_advanced if project in eligible]
    if eligible:
        advancement_rate: dict[str, Any] = {
            "status": "MEASURED",
            "numerator": len(eligible_advanced),
            "denominator": len(eligible),
            "value": len(eligible_advanced) / len(eligible),
        }
    else:
        advancement_rate = {
            "status": "NOT_APPLICABLE",
            "reason": "no independent product project was proven ELIGIBLE",
        }

    throughput_floor_met = bool(orchestrator_advanced) or not eligible
    learning = learning_evolution or {}

    kpis = {
        "independent_projects_inspected": {"value": 3, "target": 3, "status": "GREEN"},
        "product_repositories_inspected": {
            "value": product_repositories_inspected,
            "target": 5,
            "status": "GREEN" if product_repositories_inspected == 5 else "RED",
        },
        "eligible_projects": len(eligible),
        "projects_materially_advanced": len(orchestrator_advanced),
        "product_advancement_rate": advancement_rate,
        "throughput_floor_met": throughput_floor_met,
        "preventable_repeat_failure_target": 0,
    }

    return {
        "schema_version": 1,
        "generated_at": canonical["generated_at"],
        "executive_status": _executive_status(kpis),
        "canonical_inventory": {"independent_product_projects": 3, "product_repositories": 5},
        "kpis": kpis,
        "projects": canonical["projects"],
        "results": canonical["results"],
        "project_orchestrator": canonical["project_orchestrator"],
        "external_state_changes": external_state_changes,
        "no_advancement_reasons": no_advancement,
        "learning_evolution": {
            "new_failure_classes": _count_or_unknown(learning.get("new_failure_classes")),
            "root_causes_established": _count_or_unknown(learning.get("root_causes_established")),
            "regression_protections_added": _count_or_unknown(learning.get("regression_protections_added")),
            "lessons_recorded": _count_or_unknown(learning.get("lessons_recorded")),
            "prior_lessons_reused": _count_or_unknown(learning.get("prior_lessons_reused")),
            "preventable_repeat_failures": _count_or_unknown(learning.get("preventable_repeat_failures")),
            "control_plane_improvements": _count_or_unknown(learning.get("control_plane_improvements")),
            "learning_effectiveness": learning_effectiveness or {"status": "UNKNOWN"},
        },
        "jeremy_can_help": _validate_help(jeremy_can_help or []),
        "management_bottom_line": _bottom_line(kpis, no_advancement),
        "rules": [
            "UNKNOWN is preferred to invented evidence",
            "Product Reality is never inferred",
            "the advancement-rate denominator includes only projects explicitly proven ELIGIBLE",
            "only current-run ORCHESTRATOR actions count as product advancement",
            "Jeremy/manual merges and external state changes are reported separately and never satisfy the throughput floor",
            "orchestrator-only control-plane work does not count as product advancement",
            "every eligible non-advanced product must state why and identify its next gate",
        ],
    }


def _count_or_unknown(value: Any) -> int | str:
    if value is None:
        return "UNKNOWN"
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("learning KPI must be a non-negative integer or omitted")
    return value


def _validate_help(items: list[str]) -> list[str]:
    if not all(isinstance(item, str) and item.strip() for item in items):
        raise ValueError("jeremy_can_help must contain only non-empty strings")
    return [item.strip() for item in items]


def _executive_status(kpis: dict[str, Any]) -> str:
    if not kpis["throughput_floor_met"] or kpis["product_repositories_inspected"]["status"] == "RED":
        return "RED"
    rate = kpis["product_advancement_rate"]
    if rate.get("status") == "MEASURED" and rate.get("value") == 1.0:
        return "GREEN"
    return "YELLOW"


def _bottom_line(kpis: dict[str, Any], no_advancement: dict[str, str]) -> str:
    if not kpis["throughput_floor_met"]:
        return "Throughput floor missed: safe eligible product work existed but this orchestrator run produced no concrete product advancement."
    rate = kpis["product_advancement_rate"]
    if rate.get("status") == "MEASURED" and rate.get("value") == 1.0:
        return "All product projects proven eligible this run materially advanced through orchestrator-executed work."
    if no_advancement:
        return "Portfolio advanced, but at least one eligible product did not advance through this orchestrator run; inspect its recorded reason and exact next gate."
    return "No complete eligible-project denominator was proven; do not claim full portfolio utilization."
