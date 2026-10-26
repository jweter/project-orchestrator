from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
from typing import Any

from .evolution_engine import build_evolution_plan
from .learning import SECRET_PATTERNS, add_lesson, validate_lesson, validate_memory
from .repair import build_repair_plan, execute_repair_plan

CANONICAL_WORKER_STATUSES = {
    "PASS",
    "FAIL",
    "REVIEW_REQUIRED",
    "PRODUCT_REALITY_REQUIRED",
    "ENVIRONMENT_FAILURE",
}

PROJECT_REPOSITORIES = {
    "knowledge-engine": {
        "jweter/knowledge-engine-core",
        "jweter/knowledge-engine-web",
        "jweter/knowledge-engine-ai",
    },
    "rocksmith": {"jweter/rocksmith-cdlc-generator"},
    "everward": {"jweter/Project-Everward"},
    "voicestudio": {"jweter/VoiceStudio"},
    # Iron God commissioning (owner decision 2026-10-25): the control plane
    # itself is the exclusive development target. Validation only; this
    # registry never schedules work.
    "iron-god": {"jweter/project-orchestrator"},
}

NEXT_ACTIONS = {
    "VERIFIED_PASS",
    "SAFE_AUTO_REPAIR_AVAILABLE",
    "VERIFY_REPAIR",
    "ENGINEERING_REPAIR_REQUIRED",
    "GATHER_EVIDENCE",
    "ENVIRONMENT_RETRY",
    "PRODUCT_REALITY_REQUIRED",
    "HUMAN_DECISION_REQUIRED",
    "MEASURE_PREVENTION",
    "EVOLUTION_VERIFIED",
}

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class EvolutionBridgeError(ValueError):
    """Raised when worker evidence violates the unattended Evolution contract."""


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvolutionBridgeError(f"{name} must be a non-empty string")
    return value.strip()


def _bool(value: Any, name: str, *, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise EvolutionBridgeError(f"{name} must be boolean")
    return value


def _parse_timestamp(value: Any) -> str:
    text = _nonempty(value, "observed_at")
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise EvolutionBridgeError("observed_at must be valid ISO-8601") from exc
    if parsed.tzinfo is None:
        raise EvolutionBridgeError("observed_at must include a timezone")
    return text


def _contains_secret(value: Any) -> bool:
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in SECRET_PATTERNS)
    if isinstance(value, list):
        return any(_contains_secret(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_secret(key) or _contains_secret(item) for key, item in value.items())
    return False


def normalize_worker_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate one sanitized exact-head unattended-worker result.

    The bridge deliberately accepts only product project contexts declared by the portfolio bridge.
    It never guesses project/repository identity from a worker name or path.
    """

    if not isinstance(raw, dict):
        raise EvolutionBridgeError("worker_result must be an object")
    required = {
        "project",
        "repository",
        "head_sha",
        "probe_id",
        "pattern_id",
        "evidence_ref",
        "observed_at",
        "status",
        "summary",
    }
    missing = sorted(required - raw.keys())
    if missing:
        raise EvolutionBridgeError(f"worker_result missing fields: {missing}")

    project = _nonempty(raw["project"], "project")
    if project not in PROJECT_REPOSITORIES:
        raise EvolutionBridgeError("worker project is not a product project in the portfolio")
    repository = _nonempty(raw["repository"], "repository")
    if repository not in PROJECT_REPOSITORIES[project]:
        raise EvolutionBridgeError("repository does not belong to the declared project")

    head_sha = _nonempty(raw["head_sha"], "head_sha")
    if not _SHA_RE.fullmatch(head_sha):
        raise EvolutionBridgeError("head_sha must be a 40-character lowercase hex SHA")

    status = _nonempty(raw["status"], "status").replace(" ", "_")
    if status not in CANONICAL_WORKER_STATUSES:
        raise EvolutionBridgeError(f"unsupported worker status: {status}")

    normalized = {
        "schema_version": 1,
        "project": project,
        "repository": repository,
        "branch": str(raw.get("branch", "")).strip() or None,
        "head_sha": head_sha,
        "probe_id": _nonempty(raw["probe_id"], "probe_id"),
        "pattern_id": _nonempty(raw["pattern_id"], "pattern_id"),
        "evidence_ref": _nonempty(raw["evidence_ref"], "evidence_ref"),
        "observed_at": _parse_timestamp(raw["observed_at"]),
        "status": status,
        "summary": _nonempty(raw["summary"], "summary"),
        "sanitized_log_text": str(raw.get("sanitized_log_text", "")),
        "root_cause_verified": _bool(raw.get("root_cause_verified"), "root_cause_verified"),
        "repair_verified": _bool(raw.get("repair_verified"), "repair_verified"),
        "mechanism_brittle": _bool(raw.get("mechanism_brittle"), "mechanism_brittle"),
        "prevention_verified": _bool(raw.get("prevention_verified"), "prevention_verified"),
        "redesign_verified": _bool(raw.get("redesign_verified"), "redesign_verified"),
        "reserved_human_decision": _bool(raw.get("reserved_human_decision"), "reserved_human_decision"),
        "safety_regression": _bool(raw.get("safety_regression"), "safety_regression"),
        "learning_effect": raw.get("learning_effect", "UNKNOWN"),
        "occurred_after_prevention": _bool(
            raw.get("occurred_after_prevention"), "occurred_after_prevention"
        ),
    }
    if normalized["learning_effect"] not in {"PREVENTED", "SHORTENED", "NO_CHANGE", "UNKNOWN"}:
        raise EvolutionBridgeError("unsupported learning_effect")

    forbidden_fields = {
        "raw_payload",
        "private_payload",
        "credentials",
        "secret",
        "token",
    }
    if forbidden_fields.intersection(raw):
        raise EvolutionBridgeError("worker_result contains a forbidden raw/private field")
    if _contains_secret(normalized):
        raise EvolutionBridgeError("worker_result appears to contain secret material")
    return normalized


def _evolution_event(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "project": result["project"],
        "repository": result["repository"],
        "pattern_id": result["pattern_id"],
        "evidence_ref": result["evidence_ref"],
        "evidence_state": "VERIFIED",
        "event_type": "SUCCESS_PATTERN" if result["status"] == "PASS" else "FAILURE",
        "root_cause_verified": result["root_cause_verified"],
        "repair_verified": result["repair_verified"],
        "mechanism_brittle": result["mechanism_brittle"],
        "prevention_verified": result["prevention_verified"],
        "redesign_verified": result["redesign_verified"],
        "reserved_human_decision": result["reserved_human_decision"],
        "safety_regression": result["safety_regression"],
        "learning_effect": result["learning_effect"],
        "occurred_after_prevention": result["occurred_after_prevention"],
    }


def build_worker_handoff(
    *,
    worker_result: dict[str, Any],
    control_plane: dict[str, Any],
    memory: dict[str, Any],
    prior_events: list[dict[str, Any]] | None = None,
    measurements: list[dict[str, Any]] | None = None,
    repair_iteration: int = 1,
) -> dict[str, Any]:
    """Turn one exact-head worker result into the next governed engineering action."""

    result = normalize_worker_result(worker_result)
    validate_memory(memory)
    if not isinstance(control_plane, dict):
        raise EvolutionBridgeError("control_plane must be an object")
    if prior_events is not None and not isinstance(prior_events, list):
        raise EvolutionBridgeError("prior_events must be a list")
    if measurements is not None and not isinstance(measurements, list):
        raise EvolutionBridgeError("measurements must be a list")

    status = result["status"]
    base = {
        "schema_version": 1,
        "worker_result": result,
        "repair_plan": None,
        "evolution_plan": None,
        "next_action": None,
        "reason": "",
        "independent_verification_required": False,
    }

    if result["reserved_human_decision"]:
        return {
            **base,
            "next_action": "HUMAN_DECISION_REQUIRED",
            "reason": "worker evidence reaches a reserved human decision boundary",
        }
    if status == "REVIEW_REQUIRED":
        return {
            **base,
            "next_action": "GATHER_EVIDENCE",
            "reason": "worker explicitly requires review; no failure/root cause is invented",
        }
    if status == "ENVIRONMENT_FAILURE":
        return {
            **base,
            "next_action": "ENVIRONMENT_RETRY",
            "reason": "environment failure is lane-scoped and is not classified as a product defect",
        }
    if status == "PRODUCT_REALITY_REQUIRED":
        return {
            **base,
            "next_action": "PRODUCT_REALITY_REQUIRED",
            "reason": "worker cannot clear the remaining Product Reality gate",
        }

    events = list(prior_events or [])
    events.append(_evolution_event(result))
    evolution_plan = build_evolution_plan(
        project=result["project"],
        repository=result["repository"],
        pattern_id=result["pattern_id"],
        memory=memory,
        events=events,
        measurements=measurements or [],
    )

    if status == "PASS":
        if evolution_plan["evolution_eligible"]:
            action = "EVOLUTION_VERIFIED"
            reason = "verified success/prevention has measured improvement and cleared Evolution gates"
        elif evolution_plan["gate_status"] in {"MEASURE_BEFORE_PROMOTION", "REDESIGN_CANDIDATE"}:
            action = "MEASURE_PREVENTION"
            reason = "verified result exists; comparable outcome evidence is the next promotion gate"
        else:
            action = "VERIFIED_PASS"
            reason = "worker verified this exact head; no repair is required"
        return {
            **base,
            "evolution_plan": evolution_plan,
            "next_action": action,
            "reason": reason,
        }

    repair_plan = build_repair_plan(
        repository=result["repository"],
        log_text=result["sanitized_log_text"],
        control_plane=control_plane,
        iteration=repair_iteration,
    )
    if repair_plan["failure_class"] == "ENVIRONMENT":
        action = "ENVIRONMENT_RETRY"
        reason = "sanitized failure evidence classifies as environment; do not mutate product code"
    elif repair_plan["eligible_for_auto_repair"]:
        action = "SAFE_AUTO_REPAIR_AVAILABLE"
        reason = "repository-explicit deterministic safe repair is available"
    else:
        action = "ENGINEERING_REPAIR_REQUIRED"
        reason = "failure is not eligible for deterministic auto-repair; bounded engineering diagnosis is required"

    return {
        **base,
        "repair_plan": repair_plan,
        "evolution_plan": evolution_plan,
        "next_action": action,
        "reason": reason,
        "independent_verification_required": action in {
            "SAFE_AUTO_REPAIR_AVAILABLE",
            "ENGINEERING_REPAIR_REQUIRED",
        },
    }


def execute_worker_safe_repair(handoff: dict[str, Any], *, repo_root: Path) -> dict[str, Any]:
    """Execute only an already-authorized deterministic repair plan.

    This function never claims the repair worked. A separate verifier/worker/CI
    must validate the resulting exact head/worktree before promotion.
    """

    if not isinstance(handoff, dict):
        raise EvolutionBridgeError("handoff must be an object")
    if handoff.get("next_action") != "SAFE_AUTO_REPAIR_AVAILABLE":
        return {
            **handoff,
            "repair_execution": None,
            "next_action": handoff.get("next_action", "GATHER_EVIDENCE"),
        }
    plan = handoff.get("repair_plan")
    if not isinstance(plan, dict) or not plan.get("eligible_for_auto_repair"):
        raise EvolutionBridgeError("safe repair handoff is missing an executable repair_plan")

    execution = execute_repair_plan(plan, repo_root=repo_root)
    if execution.get("execution_status") != "REPAIR_COMMANDS_APPLIED":
        return {
            **handoff,
            "repair_execution": execution,
            "next_action": "ENGINEERING_REPAIR_REQUIRED",
            "reason": "repository-authorized repair command failed; escalate without guessing",
            "independent_verification_required": True,
        }
    return {
        **handoff,
        "repair_execution": execution,
        "next_action": "VERIFY_REPAIR",
        "reason": "safe repair commands applied; success remains unverified until independent exact-head checks pass",
        "independent_verification_required": True,
    }


def record_verified_repair_lesson(
    *,
    memory: dict[str, Any],
    project: str,
    repository: str,
    source_ref: str,
    symptom: str,
    root_cause: str,
    fix: str,
    verification: str,
    regression_protection: str,
    residual_risk: str,
) -> dict[str, Any]:
    """Append one independently verified repair lesson to sanitized memory."""

    if project not in PROJECT_REPOSITORIES:
        raise EvolutionBridgeError("repair lesson project is not a product project")
    if repository not in PROJECT_REPOSITORIES[project]:
        raise EvolutionBridgeError("repair lesson repository does not belong to project")
    lesson = validate_lesson(
        {
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
    )
    return add_lesson(memory, lesson)


def compile_verified_capability(
    *,
    worker_result: dict[str, Any],
    evolution_plan: dict[str, Any],
    mechanism: str,
    applicability: dict[str, Any],
) -> dict[str, Any]:
    """Compile an earned Evolution result into a scheduler-consumable capability.

    Compilation is deliberately narrower than learning-memory persistence: only a
    measured, gate-cleared EVOLUTION result can become executable scheduler advice.
    Normal repository verification and promotion gates remain authoritative.
    """

    result = normalize_worker_result(worker_result)
    if result["status"] != "PASS":
        raise EvolutionBridgeError("capability compilation requires a verified PASS worker result")
    if not isinstance(evolution_plan, dict) or not evolution_plan.get("evolution_eligible"):
        raise EvolutionBridgeError("capability compilation requires evolution_eligible evidence")
    if evolution_plan.get("gate_status") != "EVOLUTION_VERIFIED":
        raise EvolutionBridgeError("capability compilation requires EVOLUTION_VERIFIED gate status")
    if evolution_plan.get("repository") != result["repository"]:
        raise EvolutionBridgeError("capability repository does not match worker evidence")
    if evolution_plan.get("pattern_id") != result["pattern_id"]:
        raise EvolutionBridgeError("capability pattern does not match worker evidence")
    if not isinstance(mechanism, str) or not mechanism.strip():
        raise EvolutionBridgeError("capability mechanism is required")
    if not isinstance(applicability, dict) or not applicability:
        raise EvolutionBridgeError("capability applicability is required")

    import hashlib
    import json

    seed = json.dumps(
        [result["repository"], result["pattern_id"], mechanism.strip(), applicability],
        sort_keys=True,
    )
    return {
        "rule_id": "cap-" + hashlib.sha256(seed.encode()).hexdigest()[:12],
        "repository": result["repository"],
        "scope": "repository",
        "mechanism": mechanism.strip(),
        "applicability": applicability,
        "evidence_refs": list(evolution_plan.get("verified_evidence_refs", [])),
        "safety_invariants": list(evolution_plan.get("safety_boundaries", [])),
        "status": "VERIFIED_RETAINED",
        "authority": "ADVISORY_TO_SCHEDULER_NORMAL_GATES_REQUIRED",
        "source_pattern_id": result["pattern_id"],
        "source_head_sha": result["head_sha"],
        "measurement_summary": list(evolution_plan.get("measurement_summary", [])),
    }
