from __future__ import annotations

from typing import Any

from .learning import add_lesson, validate_memory
from .repair import fingerprint_failure


_PROJECT_BY_REPO = {
    "jweter/project-orchestrator": "portfolio",
    "jweter/knowledge-engine-core": "knowledge-engine",
    "jweter/knowledge-engine-web": "knowledge-engine",
    "jweter/knowledge-engine-ai": "knowledge-engine",
    "jweter/rocksmith-cdlc-generator": "rocksmith",
    "jweter/Project-Everward": "everward",
    "jweter/VoiceStudio": "voicestudio",
}


def verified_failure_lesson(
    *,
    repository: str,
    source_ref: str,
    log_text: str,
    root_cause: str,
    fix: str,
    verification: str,
    regression_protection: str,
    residual_risk: str,
) -> dict[str, Any]:
    project = _PROJECT_BY_REPO.get(repository)
    if project is None:
        raise ValueError("repository is not an authorized learning repository")
    fingerprint = fingerprint_failure(log_text)
    if fingerprint.confidence != "HIGH" or fingerprint.failure_class == "UNKNOWN":
        raise ValueError("failure fingerprint is not deterministic enough to learn")
    if not root_cause.strip() or not fix.strip() or not verification.strip():
        raise ValueError("verified diagnosis, fix, and verification are required")
    symptom = f"{fingerprint.failure_class}: " + " | ".join(fingerprint.evidence[:2])
    return {
        "project": project,
        "repository": repository,
        "source_type": "ci_failure",
        "source_ref": source_ref,
        "symptom": symptom,
        "root_cause": root_cause,
        "fix": fix,
        "verification": verification,
        "regression_protection": regression_protection,
        "residual_risk": residual_risk,
        "evidence_state": "VERIFIED",
    }


def persist_verified_failure(memory: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    validate_memory(memory)
    return add_lesson(memory, verified_failure_lesson(**kwargs))
