from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_KEYS = ("knowledge-engine", "rocksmith", "everward", "voicestudio")
PROMOTION_STATES = {
    "IMPLEMENTING",
    "PREFLIGHT_UNVERIFIED",
    "PREFLIGHT_GREEN",
    "PR_OPEN",
    "CI_PENDING",
    "GREEN",
    "MERGED",
    "PRODUCT_REALITY_PENDING",
    "PRODUCT_REALITY_VERIFIED",
    "PARKED_FOR_RETRY",
    "NO_SAFE_ELIGIBLE_WORK",
}
RESULT_KEYS = (
    "MERGED",
    "FIXED",
    "NEW_PRS",
    "WAITING_ON_PREFLIGHT",
    "WAITING_ON_CI",
    "BLOCKED",
    "NEEDS_JEREMY",
)


class RunReportError(ValueError):
    pass


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RunReportError(f"{field} must be non-empty text")
    return value.strip()


def validate_run_report(report: dict[str, Any]) -> dict[str, Any]:
    if report.get("schema_version") != 1:
        raise RunReportError("schema_version must be 1")
    _require_text(report.get("generated_at"), "generated_at")

    projects = report.get("projects")
    if not isinstance(projects, dict) or tuple(projects) != PROJECT_KEYS:
        raise RunReportError("projects must contain knowledge-engine, rocksmith, everward, voicestudio in canonical order")

    for project, entry in projects.items():
        if not isinstance(entry, dict):
            raise RunReportError(f"projects.{project} must be an object")
        state = _require_text(entry.get("promotion_state"), f"projects.{project}.promotion_state")
        if state not in PROMOTION_STATES:
            raise RunReportError(f"unsupported promotion_state {state}")
        _require_text(entry.get("implementation_verification"), f"projects.{project}.implementation_verification")
        _require_text(entry.get("active_ownership"), f"projects.{project}.active_ownership")
        _require_text(entry.get("next_gate"), f"projects.{project}.next_gate")
        reason = entry.get("reason")
        if state in {"PARKED_FOR_RETRY", "NO_SAFE_ELIGIBLE_WORK"}:
            _require_text(reason, f"projects.{project}.reason")
        elif reason is not None and not isinstance(reason, str):
            raise RunReportError(f"projects.{project}.reason must be text or null")
        debt = entry.get("product_reality_debt")
        if not isinstance(debt, list) or any(not isinstance(item, str) for item in debt):
            raise RunReportError(f"projects.{project}.product_reality_debt must be a string list")

    results = report.get("results")
    if not isinstance(results, dict) or tuple(results) != RESULT_KEYS:
        raise RunReportError("results must contain the canonical result buckets in order")
    for key, values in results.items():
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise RunReportError(f"results.{key} must be a string list")

    orchestrator = report.get("project_orchestrator")
    if not isinstance(orchestrator, dict):
        raise RunReportError("project_orchestrator must be an object")
    _require_text(orchestrator.get("state"), "project_orchestrator.state")
    _require_text(orchestrator.get("next_gate"), "project_orchestrator.next_gate")

    return report


def build_run_report(
    *,
    projects: dict[str, dict[str, Any]],
    results: dict[str, list[str]],
    project_orchestrator: dict[str, Any],
    generated_at: str | None = None,
) -> dict[str, Any]:
    unknown_result_keys = set(results) - set(RESULT_KEYS)
    if unknown_result_keys:
        raise RunReportError(f"unsupported result buckets: {sorted(unknown_result_keys)}")
    report = {
        "schema_version": 1,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "projects": {key: projects[key] for key in PROJECT_KEYS},
        "results": {key: results.get(key, []) for key in RESULT_KEYS},
        "project_orchestrator": project_orchestrator,
    }
    return validate_run_report(report)


def write_run_report(path: Path, report: dict[str, Any]) -> None:
    validated = validate_run_report(report)
    path.write_text(json.dumps(validated, indent=2, sort_keys=False) + "\n", encoding="utf-8")
