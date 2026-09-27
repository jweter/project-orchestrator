from __future__ import annotations

from collections import Counter
import re
from typing import Any

from .learning import validate_memory

STAGES = ("REPAIR", "REPLACEMENT", "UPGRADE", "REDESIGN", "EVOLUTION")
ALLOWED_PROJECTS = {"portfolio", "knowledge-engine", "rocksmith", "everward", "voicestudio"}
ALLOWED_EVENT_TYPES = {"FAILURE", "SUCCESS_PATTERN"}
ALLOWED_LEARNING_EFFECTS = {"PREVENTED", "SHORTENED", "NO_CHANGE", "UNKNOWN"}
ALLOWED_DIRECTIONS = {"lower_is_better", "higher_is_better"}
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class EvolutionEngineError(ValueError):
    """Raised when Evolution Engine evidence violates the governed contract."""


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvolutionEngineError(f"{name} must be a non-empty string")
    return value.strip()


def _bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise EvolutionEngineError(f"{name} must be boolean")
    return value


def _normalize_event(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise EvolutionEngineError("evolution events must be objects")
    required = {
        "project",
        "repository",
        "pattern_id",
        "evidence_ref",
        "evidence_state",
        "event_type",
        "root_cause_verified",
        "repair_verified",
        "mechanism_brittle",
        "prevention_verified",
        "redesign_verified",
        "reserved_human_decision",
        "safety_regression",
    }
    missing = sorted(required - raw.keys())
    if missing:
        raise EvolutionEngineError(f"evolution event missing fields: {missing}")

    project = _nonempty(raw["project"], "project")
    if project not in ALLOWED_PROJECTS:
        raise EvolutionEngineError("event project is not in the portfolio")
    event_type = _nonempty(raw["event_type"], "event_type")
    if event_type not in ALLOWED_EVENT_TYPES:
        raise EvolutionEngineError("unsupported event_type")
    learning_effect = raw.get("learning_effect", "UNKNOWN")
    if learning_effect not in ALLOWED_LEARNING_EFFECTS:
        raise EvolutionEngineError("unsupported learning_effect")

    return {
        "project": project,
        "repository": _nonempty(raw["repository"], "repository"),
        "pattern_id": _nonempty(raw["pattern_id"], "pattern_id"),
        "evidence_ref": _nonempty(raw["evidence_ref"], "evidence_ref"),
        "evidence_state": _nonempty(raw["evidence_state"], "evidence_state"),
        "event_type": event_type,
        "root_cause_verified": _bool(raw["root_cause_verified"], "root_cause_verified"),
        "repair_verified": _bool(raw["repair_verified"], "repair_verified"),
        "mechanism_brittle": _bool(raw["mechanism_brittle"], "mechanism_brittle"),
        "prevention_verified": _bool(raw["prevention_verified"], "prevention_verified"),
        "redesign_verified": _bool(raw["redesign_verified"], "redesign_verified"),
        "reserved_human_decision": _bool(raw["reserved_human_decision"], "reserved_human_decision"),
        "safety_regression": _bool(raw["safety_regression"], "safety_regression"),
        "learning_effect": learning_effect,
        "occurred_after_prevention": _bool(
            raw.get("occurred_after_prevention", False), "occurred_after_prevention"
        ),
    }


def _normalize_measurement(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise EvolutionEngineError("measurements must be objects")
    required = {
        "repository",
        "pattern_id",
        "evidence_ref",
        "evidence_state",
        "metric",
        "direction",
        "before",
        "after",
        "sample_count_before",
        "sample_count_after",
    }
    missing = sorted(required - raw.keys())
    if missing:
        raise EvolutionEngineError(f"measurement missing fields: {missing}")
    direction = _nonempty(raw["direction"], "direction")
    if direction not in ALLOWED_DIRECTIONS:
        raise EvolutionEngineError("unsupported measurement direction")
    for field in ("before", "after"):
        value = raw[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise EvolutionEngineError(f"{field} must be numeric")
    for field in ("sample_count_before", "sample_count_after"):
        value = raw[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise EvolutionEngineError(f"{field} must be a positive integer")

    return {
        "repository": _nonempty(raw["repository"], "repository"),
        "pattern_id": _nonempty(raw["pattern_id"], "pattern_id"),
        "evidence_ref": _nonempty(raw["evidence_ref"], "evidence_ref"),
        "evidence_state": _nonempty(raw["evidence_state"], "evidence_state"),
        "metric": _nonempty(raw["metric"], "metric"),
        "direction": direction,
        "before": float(raw["before"]),
        "after": float(raw["after"]),
        "sample_count_before": raw["sample_count_before"],
        "sample_count_after": raw["sample_count_after"],
    }


def _measurement_outcome(measurement: dict[str, Any], *, minimum_sample_count: int) -> str:
    if (
        measurement["sample_count_before"] < minimum_sample_count
        or measurement["sample_count_after"] < minimum_sample_count
    ):
        return "INSUFFICIENT_DATA"
    before = measurement["before"]
    after = measurement["after"]
    if before == after:
        return "UNCHANGED"
    improved = (
        after < before
        if measurement["direction"] == "lower_is_better"
        else after > before
    )
    return "IMPROVED" if improved else "DEGRADED"


def _related_lessons(
    memory: dict[str, Any],
    *,
    project: str,
    repository: str,
    pattern_id: str,
) -> list[str]:
    tokens = {
        token
        for token in re.split(r"[^a-z0-9]+", pattern_id.lower())
        if len(token) >= 4
    }
    result: list[str] = []
    for lesson in memory["entries"]:
        same_repo = lesson["repository"] == repository
        portfolio_process = (
            lesson["project"] == "portfolio"
            and lesson["repository"] == "jweter/project-orchestrator"
        )
        if not (same_repo or portfolio_process):
            continue
        if lesson["project"] not in {project, "portfolio"}:
            continue
        haystack = " ".join(
            (
                lesson["symptom"],
                lesson["root_cause"],
                lesson["fix"],
                lesson["regression_protection"],
            )
        ).lower()
        if tokens and not any(token in haystack for token in tokens):
            continue
        result.append(lesson["lesson_id"])
    return sorted(set(result))


def _recommended_actions(
    stage: str, *, repeated: bool, failure_after_prevention: bool
) -> list[str]:
    if stage == "REPAIR":
        actions = [
            "apply or preserve the smallest verified bounded repair",
            "verify the original failure is gone at the exact head",
            "persist root cause, fix, verification, regression protection, and residual risk",
        ]
        if repeated:
            actions.append(
                "evaluate whether recurrence proves the repaired mechanism is brittle or prevention belongs earlier"
            )
        return actions
    if stage == "REPLACEMENT":
        return [
            "replace only the bounded brittle mechanism; do not broaden product scope",
            "prove parity or improvement against the original acceptance contract",
            "add regression protection before retiring the old mechanism",
        ]
    if stage == "UPGRADE":
        actions = [
            "move the verified prevention mechanism earlier in the lifecycle",
            "measure whether later incidents are prevented or shortened",
            "retain exact-head preflight, review, CI, policy, and Product Reality gates",
        ]
        if failure_after_prevention:
            actions.append(
                "because the failure recurred after prevention, prepare a bounded structural redesign proposal"
            )
        return actions
    if stage == "REDESIGN":
        return [
            "implement structural change only through normal roadmap and architecture authority",
            "preserve rollback and compatibility boundaries",
            "collect before/after measurements before any portfolio-wide promotion",
        ]
    return [
        "standardize the proven engineering capability where repository authority permits",
        "continue measuring outcomes for regression or unintended gate weakening",
        "reuse the capability without transferring unrelated product requirements between projects",
    ]


def build_evolution_plan(
    *,
    project: str,
    repository: str,
    pattern_id: str,
    memory: dict[str, Any],
    events: list[dict[str, Any]],
    measurements: list[dict[str, Any]] | None = None,
    minimum_evolution_sample_count: int = 2,
) -> dict[str, Any]:
    """Build a governed Fix_It-style engineering evolution plan from verified evidence.

    The engine is advisory/planning authority only. It never edits repositories,
    changes policy, crosses reserved human-decision boundaries, or promotes an
    improvement without measured evidence.
    """

    project = _nonempty(project, "project")
    if project not in ALLOWED_PROJECTS:
        raise EvolutionEngineError("project is not in the portfolio")
    repository = _nonempty(repository, "repository")
    pattern_id = _nonempty(pattern_id, "pattern_id")
    if (
        isinstance(minimum_evolution_sample_count, bool)
        or not isinstance(minimum_evolution_sample_count, int)
        or minimum_evolution_sample_count < 1
    ):
        raise EvolutionEngineError(
            "minimum_evolution_sample_count must be a positive integer"
        )

    normalized_memory = validate_memory(memory)
    if not isinstance(events, list) or not events:
        raise EvolutionEngineError("events must be a non-empty list")
    normalized_events = [_normalize_event(event) for event in events]

    matched = [
        event
        for event in normalized_events
        if event["project"] == project
        and event["repository"] == repository
        and event["pattern_id"] == pattern_id
    ]
    if not matched:
        raise EvolutionEngineError(
            "no verified evidence events match the requested project/repository/pattern"
        )

    verified = [event for event in matched if event["evidence_state"] == "VERIFIED"]
    ignored_unverified = len(matched) - len(verified)
    if not verified:
        return {
            "schema_version": 1,
            "project": project,
            "repository": repository,
            "pattern_id": pattern_id,
            "current_stage": "REPAIR",
            "recommended_next_stage": "REPAIR",
            "gate_status": "HOLD_GATHER_EVIDENCE",
            "evolution_eligible": False,
            "reason": "no VERIFIED evidence is available; fail closed",
            "verified_evidence_refs": [],
            "ignored_unverified_event_count": ignored_unverified,
            "related_verified_lessons": [],
            "measurement_summary": [],
            "learning_effects": {},
            "recommended_actions": [
                "gather trustworthy evidence without inventing root cause or success"
            ],
            "verification_plan": [
                "obtain VERIFIED evidence bound to the exact repository/work state"
            ],
            "rollback_boundary": "no change authorized",
            "human_gate_required": False,
            "execution_authority": "PLAN_ONLY",
        }

    failures = [event for event in verified if event["event_type"] == "FAILURE"]
    successes = [
        event for event in verified if event["event_type"] == "SUCCESS_PATTERN"
    ]
    recurrence_count = len({event["evidence_ref"] for event in failures})
    repeated = recurrence_count >= 2
    root_cause_verified = bool(failures) and all(
        event["root_cause_verified"] for event in failures
    )
    repair_verified = any(event["repair_verified"] for event in verified)
    brittle = repeated and any(event["mechanism_brittle"] for event in failures)
    prevention_verified = any(event["prevention_verified"] for event in verified)
    redesign_verified = any(event["redesign_verified"] for event in verified)
    failure_after_prevention = any(
        event["event_type"] == "FAILURE" and event["occurred_after_prevention"]
        for event in verified
    )
    human_gate_required = any(
        event["reserved_human_decision"] for event in verified
    )
    safety_regression = any(event["safety_regression"] for event in verified)

    if redesign_verified:
        stage = "REDESIGN"
    elif prevention_verified or (not failures and len(successes) >= 2):
        stage = "UPGRADE"
    elif brittle:
        stage = "REPLACEMENT"
    else:
        stage = "REPAIR"

    normalized_measurements = []
    for raw in measurements or []:
        measurement = _normalize_measurement(raw)
        if (
            measurement["repository"] == repository
            and measurement["pattern_id"] == pattern_id
            and measurement["evidence_state"] == "VERIFIED"
        ):
            measurement = {
                **measurement,
                "outcome": _measurement_outcome(
                    measurement,
                    minimum_sample_count=minimum_evolution_sample_count,
                ),
            }
            normalized_measurements.append(measurement)

    outcomes = Counter(measurement["outcome"] for measurement in normalized_measurements)
    measured_improvement = outcomes["IMPROVED"] > 0
    measured_degradation = outcomes["DEGRADED"] > 0
    sufficient_measurements = (
        outcomes["IMPROVED"] + outcomes["DEGRADED"] + outcomes["UNCHANGED"] > 0
    )

    candidate_verified = (
        prevention_verified
        or redesign_verified
        or (stage == "REPLACEMENT" and repair_verified)
    )
    evolution_eligible = (
        candidate_verified
        and measured_improvement
        and sufficient_measurements
        and not measured_degradation
        and not safety_regression
        and not human_gate_required
    )
    if evolution_eligible:
        stage = "EVOLUTION"

    if human_gate_required:
        gate_status = "HUMAN_DECISION_REQUIRED"
        reason = (
            "verified evidence reaches a reserved human product/architecture/security/"
            "cost/release/irreversible decision boundary"
        )
    elif safety_regression:
        gate_status = "BLOCKED_SAFETY_REGRESSION"
        reason = "verified evidence reports a safety/policy regression; evolution is forbidden"
    elif stage == "EVOLUTION":
        gate_status = "EVOLUTION_VERIFIED"
        reason = (
            "verified improvement has measured benefit with no measured degradation or safety regression"
        )
    elif stage == "REDESIGN":
        gate_status = "MEASURE_BEFORE_PROMOTION" if redesign_verified else "PROPOSAL_ONLY"
        reason = (
            "structural change requires governed verification and before/after evidence before evolution"
        )
    elif stage == "UPGRADE":
        gate_status = (
            "REDESIGN_CANDIDATE" if failure_after_prevention else "MEASURE_BEFORE_PROMOTION"
        )
        reason = (
            "verified prevention exists but the failure recurred after prevention"
            if failure_after_prevention
            else "verified prevention exists; measure later outcomes before portfolio promotion"
        )
    elif stage == "REPLACEMENT":
        gate_status = "REPLACEMENT_CANDIDATE"
        reason = (
            "repeated verified failures show evidence that the bounded mechanism is brittle"
        )
    else:
        gate_status = "REPAIR_AND_LEARN"
        reason = (
            "a verified failure needs the smallest bounded correction first"
            if failures
            else "a verified success pattern exists but has not yet earned portfolio promotion"
        )

    if stage == "EVOLUTION":
        next_stage = "EVOLUTION"
    elif stage == "REDESIGN":
        next_stage = "EVOLUTION"
    elif stage == "UPGRADE":
        next_stage = "REDESIGN" if failure_after_prevention else "EVOLUTION"
    elif stage == "REPLACEMENT":
        next_stage = "UPGRADE"
    elif brittle:
        next_stage = "REPLACEMENT"
    elif repeated or repair_verified:
        next_stage = "UPGRADE"
    else:
        next_stage = "REPAIR"

    learning_effects = Counter(event["learning_effect"] for event in verified)
    related_lessons = _related_lessons(
        normalized_memory,
        project=project,
        repository=repository,
        pattern_id=pattern_id,
    )

    verification_plan = [
        "bind all evidence to exact repository work state",
        "reproduce the original failure or acceptance condition where practical",
        "prove the selected repair/prevention removes the defect without weakening existing gates",
        "run canonical exact-head preflight plus fresh independent PR CI/review",
    ]
    if next_stage == "EVOLUTION":
        verification_plan.append(
            "collect comparable before/after measurements and reject promotion on any measured degradation"
        )
    if next_stage == "REDESIGN":
        verification_plan.append(
            "obtain normal roadmap/architecture authorization before structural redesign"
        )

    return {
        "schema_version": 1,
        "project": project,
        "repository": repository,
        "pattern_id": pattern_id,
        "current_stage": stage,
        "recommended_next_stage": next_stage,
        "gate_status": gate_status,
        "evolution_eligible": evolution_eligible,
        "reason": reason,
        "verified_evidence_refs": sorted(
            {event["evidence_ref"] for event in verified}
        ),
        "ignored_unverified_event_count": ignored_unverified,
        "failure_recurrence_count": recurrence_count,
        "root_cause_verified": root_cause_verified,
        "repair_verified": repair_verified,
        "mechanism_brittle": brittle,
        "prevention_verified": prevention_verified,
        "redesign_verified": redesign_verified,
        "failure_after_prevention": failure_after_prevention,
        "related_verified_lessons": related_lessons,
        "learning_effects": dict(sorted(learning_effects.items())),
        "measurement_summary": [
            {
                "metric": measurement["metric"],
                "outcome": measurement["outcome"],
                "before": measurement["before"],
                "after": measurement["after"],
                "direction": measurement["direction"],
                "sample_count_before": measurement["sample_count_before"],
                "sample_count_after": measurement["sample_count_after"],
                "evidence_ref": measurement["evidence_ref"],
            }
            for measurement in sorted(
                normalized_measurements,
                key=lambda item: (item["metric"], item["evidence_ref"]),
            )
        ],
        "recommended_actions": _recommended_actions(
            stage,
            repeated=repeated,
            failure_after_prevention=failure_after_prevention,
        ),
        "verification_plan": verification_plan,
        "rollback_boundary": (
            "preserve the prior verified mechanism until the candidate replacement/upgrade/"
            "redesign has independent verification; revert on regression or policy-boundary weakening"
        ),
        "human_gate_required": human_gate_required,
        "execution_authority": "PLAN_ONLY",
        "safety_boundaries": [
            "never weaken CI, review, security, privacy, provenance, licensing, architecture, or Product Reality gates",
            "never promote UNKNOWN or unverified evidence",
            "never cross a reserved human decision boundary autonomously",
            "never transfer unrelated product requirements between projects",
            "portfolio evolution may reuse engineering process/tooling patterns only where repository authority permits",
        ],
    }


def evaluate_material_review_gate(
    *,
    current_head_sha: str,
    reviewed_head_sha: str,
    review_state: str,
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Fail closed on unresolved material current-head findings regardless of review state."""

    for name, value in (
        ("current_head_sha", current_head_sha),
        ("reviewed_head_sha", reviewed_head_sha),
    ):
        if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
            raise EvolutionEngineError(
                f"{name} must be a 40-character lowercase hex SHA"
            )
    review_state = _nonempty(review_state, "review_state")
    if not isinstance(findings, list):
        raise EvolutionEngineError("findings must be a list")

    if reviewed_head_sha != current_head_sha:
        return {
            "schema_version": 1,
            "merge_allowed": False,
            "failure_class": "REVIEW_FAILURE",
            "state": "REVIEW_REVALIDATION_REQUIRED",
            "reason": "review evidence is not bound to the current PR head",
            "blocking_finding_ids": [],
        }

    blocking: list[str] = []
    for raw in findings:
        if not isinstance(raw, dict):
            raise EvolutionEngineError("review findings must be objects")
        required = {"finding_id", "material", "resolved", "outdated"}
        missing = sorted(required - raw.keys())
        if missing:
            raise EvolutionEngineError(f"review finding missing fields: {missing}")
        finding_id = _nonempty(raw["finding_id"], "finding_id")
        material = _bool(raw["material"], "material")
        resolved = _bool(raw["resolved"], "resolved")
        outdated = _bool(raw["outdated"], "outdated")
        if material and not resolved and not outdated:
            blocking.append(finding_id)

    if review_state == "CHANGES_REQUESTED" or blocking:
        return {
            "schema_version": 1,
            "merge_allowed": False,
            "failure_class": "REVIEW_FAILURE",
            "state": "MATERIAL_REVIEW_FINDING_BLOCKS_MERGE",
            "reason": (
                "unresolved material correctness/safety/architecture/provenance/privacy/licensing finding exists"
                if blocking
                else "review state requests changes"
            ),
            "blocking_finding_ids": sorted(blocking),
        }

    return {
        "schema_version": 1,
        "merge_allowed": True,
        "failure_class": None,
        "state": "REVIEW_CLEAR",
        "reason": "current-head review has no unresolved material finding",
        "blocking_finding_ids": [],
    }

\n