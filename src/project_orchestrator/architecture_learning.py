from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

_ALLOWED_OUTCOMES = {"SUCCESS", "FAILURE", "MIXED"}
_ALLOWED_EVIDENCE = {"VERIFIED", "VERIFIED_SANITIZED"}


@dataclass(frozen=True)
class EngineeringObservation:
    repository: str
    area: str
    pattern: str
    outcome: str
    evidence_state: str
    source_ref: str
    verification: str
    maintenance_signal: str = ""


def learn_engineering_patterns(
    observations: Iterable[dict[str, Any]],
    *,
    repository: str,
    area: str,
    minimum_support: int = 2,
) -> dict[str, Any]:
    """Derive conservative code/architecture guidance from verified outcomes.

    A merge is not evidence that a pattern is good. Patterns are promoted only
    when repeated verified outcomes agree and no verified failure contradicts
    them. Mixed evidence is surfaced for review rather than converted into a
    recommendation. Output is deterministic and repository/area scoped.
    """
    if not repository or not area:
        raise ValueError("repository and area must be non-empty")
    if minimum_support < 2:
        raise ValueError("minimum_support must be at least 2")

    normalized = [_normalize(item) for item in observations]
    scoped = [item for item in normalized if item.repository == repository and item.area == area]
    by_pattern: dict[str, list[EngineeringObservation]] = {}
    for item in scoped:
        by_pattern.setdefault(item.pattern, []).append(item)

    trusted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    uncertain: list[dict[str, Any]] = []
    for pattern in sorted(by_pattern):
        items = by_pattern[pattern]
        successes = [item for item in items if item.outcome == "SUCCESS"]
        failures = [item for item in items if item.outcome == "FAILURE"]
        mixed = [item for item in items if item.outcome == "MIXED"]
        record = {
            "pattern": pattern,
            "verified_observations": len(items),
            "success_count": len(successes),
            "failure_count": len(failures),
            "mixed_count": len(mixed),
            "source_refs": sorted({item.source_ref for item in items}),
            "maintenance_signals": sorted({item.maintenance_signal for item in items if item.maintenance_signal}),
        }
        if len(successes) >= minimum_support and not failures and not mixed:
            trusted.append({**record, "classification": "REUSE_CANDIDATE"})
        elif len(failures) >= minimum_support and not successes:
            rejected.append({**record, "classification": "AVOID_CANDIDATE"})
        else:
            uncertain.append({**record, "classification": "INSUFFICIENT_OR_CONFLICTING_EVIDENCE"})

    return {
        "schema_version": 1,
        "repository": repository,
        "area": area,
        "trusted_patterns": trusted,
        "avoid_patterns": rejected,
        "uncertain_patterns": uncertain,
        "authority_note": "Engineering-pattern learning is advisory and never overrides repository architecture, security/privacy, licensing/provenance, verification, merge, or Product Reality authority.",
        "quality_rule": "Optimize for verified maintainable capability, not lines of code, commits, PR count, or merge occurrence.",
    }


def retrieve_architecture_guidance(
    learned: dict[str, Any], *, candidate_patterns: Iterable[str]
) -> dict[str, Any]:
    """Return exact learned guidance for a proposed implementation structure."""
    candidates = sorted({value.strip() for value in candidate_patterns if isinstance(value, str) and value.strip()})
    trusted = {item["pattern"]: item for item in learned.get("trusted_patterns", [])}
    avoid = {item["pattern"]: item for item in learned.get("avoid_patterns", [])}
    return {
        "schema_version": 1,
        "reuse": [trusted[value] for value in candidates if value in trusted],
        "avoid": [avoid[value] for value in candidates if value in avoid],
        "unknown": [value for value in candidates if value not in trusted and value not in avoid],
        "decision_rule": "UNKNOWN remains unknown; absence of negative evidence is not evidence of a good architecture.",
    }


def _normalize(payload: dict[str, Any]) -> EngineeringObservation:
    if not isinstance(payload, dict):
        raise ValueError("observation must be an object")
    required = {"repository", "area", "pattern", "outcome", "evidence_state", "source_ref", "verification"}
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"observation missing required fields: {missing}")
    values = {key: payload[key] for key in required}
    if not all(isinstance(value, str) and value.strip() for value in values.values()):
        raise ValueError("observation fields must be non-empty strings")
    if payload["outcome"] not in _ALLOWED_OUTCOMES:
        raise ValueError("unsupported outcome")
    if payload["evidence_state"] not in _ALLOWED_EVIDENCE:
        raise ValueError("only verified observations may train architecture learning")
    return EngineeringObservation(
        repository=payload["repository"],
        area=payload["area"],
        pattern=payload["pattern"],
        outcome=payload["outcome"],
        evidence_state=payload["evidence_state"],
        source_ref=payload["source_ref"],
        verification=payload["verification"],
        maintenance_signal=str(payload.get("maintenance_signal", "")),
    )
