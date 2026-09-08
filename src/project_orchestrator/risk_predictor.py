from __future__ import annotations

from collections import defaultdict
from typing import Any

from .area_learning import retrieve_learning
from .learning import validate_memory
from .repair import fingerprint_failure


def build_pre_change_risk_profile(
    memory: dict[str, Any],
    *,
    repository: str,
    changed_paths: list[str],
    change_summary: str = "",
    limit: int = 12,
) -> dict[str, Any]:
    """Build an evidence-only pre-change risk profile from verified history.

    This is historical risk signaling, not statistical prediction. It never
    invents probability, severity, ROI, or authority. The output tells the
    engineering agent which verified failure classes and successful patterns
    have matched the planned area before mutation begins.
    """
    normalized = validate_memory(memory)
    retrieval = retrieve_learning(
        normalized,
        repository=repository,
        changed_paths=changed_paths,
        change_summary=change_summary,
        limit=limit,
    )
    lessons_by_id = {entry["lesson_id"]: entry for entry in normalized["entries"]}

    failure_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    success_patterns: list[dict[str, Any]] = []

    for match in retrieval["relevant_lessons"]:
        lesson = lessons_by_id[match["lesson_id"]]
        if lesson["source_type"] == "success_pattern":
            success_patterns.append(
                {
                    "lesson_id": lesson["lesson_id"],
                    "source_ref": lesson["source_ref"],
                    "matched_paths": match["matched_paths"],
                    "matched_tokens": match["matched_tokens"],
                    "successful_strategy": lesson["fix"],
                    "verification": lesson["verification"],
                    "reuse_rule": lesson["regression_protection"],
                    "residual_risk": lesson["residual_risk"],
                }
            )
            continue

        fingerprint_text = "\n".join(
            [
                lesson["symptom"],
                lesson["root_cause"],
                lesson["fix"],
                lesson["verification"],
                lesson["regression_protection"],
                lesson["residual_risk"],
            ]
        )
        fingerprint = fingerprint_failure(fingerprint_text)
        failure_groups[fingerprint.failure_class].append(
            {
                "lesson_id": lesson["lesson_id"],
                "source_type": lesson["source_type"],
                "source_ref": lesson["source_ref"],
                "matched_paths": match["matched_paths"],
                "matched_tokens": match["matched_tokens"],
                "classification_confidence": fingerprint.confidence,
                "root_cause": lesson["root_cause"],
                "prevention_rule": lesson["regression_protection"],
                "residual_risk": lesson["residual_risk"],
            }
        )

    historical_failure_classes = []
    for failure_class in sorted(failure_groups):
        incidents = failure_groups[failure_class]
        historical_failure_classes.append(
            {
                "failure_class": failure_class,
                "verified_matching_incident_count": len(incidents),
                "evidence_refs": sorted({item["source_ref"] for item in incidents}),
                "prevention_rules": sorted({item["prevention_rule"] for item in incidents}),
                "incidents": incidents,
            }
        )

    prevention_checklist = sorted(
        {
            rule
            for group in historical_failure_classes
            for rule in group["prevention_rules"]
        }
        | {item["reuse_rule"] for item in success_patterns}
    )

    if historical_failure_classes or success_patterns:
        gate = "HISTORICAL_SIGNALS_PRESENT"
    else:
        gate = "NO_RELEVANT_VERIFIED_HISTORY"

    return {
        "schema_version": 1,
        "repository": repository,
        "changed_paths": retrieval["changed_paths"],
        "change_summary": change_summary,
        "historical_signal_state": gate,
        "verified_lessons_considered": retrieval["verified_lessons_considered"],
        "matched_verified_lessons": len(retrieval["relevant_lessons"]),
        "historical_failure_classes": historical_failure_classes,
        "successful_patterns": success_patterns,
        "prevention_checklist": prevention_checklist,
        "prediction_semantics": "historical evidence signal only; not a probability or forecast",
        "risk_probability": "UNKNOWN",
        "risk_severity": "UNKNOWN",
        "required_agent_behavior": (
            "apply relevant prevention/reuse rules before mutation and preserve normal repository authority"
            if prevention_checklist
            else "proceed without invented historical risk; normal repository authority and verification still apply"
        ),
        "authority_note": (
            "historical signals inform implementation and verification planning but never override "
            "roadmap, architecture, security, licensing/provenance, privacy, CI, or Product Reality authority"
        ),
    }
