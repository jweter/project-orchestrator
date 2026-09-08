from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .learning import validate_memory
from .timeseries import validate_store

_ALLOWED_FRICTION = {
    "repeat_ci_failure",
    "repeat_preflight_failure",
    "branch_reconciliation_friction",
    "stale_branch_accumulation",
    "manual_intervention",
    "product_reality_false_freeze",
    "evidence_gap",
    "orchestration_control_failure",
}


def build_self_improvement_proposals(
    memory: dict[str, Any],
    store: dict[str, Any],
    friction_events: list[dict[str, Any]],
    *,
    minimum_recurrence: int = 2,
) -> dict[str, Any]:
    """Build bounded self-improvement proposals from verified recurring friction.

    This planner is proposal-only. It does not mutate repositories, open PRs,
    change policy, weaken gates, merge code, or claim predicted ROI.
    """
    normalized_memory = validate_memory(memory)
    normalized_store = validate_store(store)
    if not isinstance(friction_events, list):
        raise ValueError("friction_events must be a list")
    if not isinstance(minimum_recurrence, int) or isinstance(minimum_recurrence, bool) or minimum_recurrence < 2:
        raise ValueError("minimum_recurrence must be an integer >= 2")

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in friction_events:
        if not isinstance(event, dict):
            raise ValueError("each friction event must be an object")
        required = {"repository", "friction_type", "evidence_ref", "summary", "evidence_state"}
        missing = required - event.keys()
        if missing:
            raise ValueError("friction event missing fields: " + ", ".join(sorted(missing)))
        if event["friction_type"] not in _ALLOWED_FRICTION:
            raise ValueError(f"unsupported friction_type: {event['friction_type']}")
        if event["evidence_state"] != "VERIFIED":
            continue
        if not all(isinstance(event[k], str) and event[k].strip() for k in ("repository", "evidence_ref", "summary")):
            raise ValueError("repository, evidence_ref, and summary must be non-empty strings")
        grouped[(event["repository"], event["friction_type"])].append(event)

    proposals: list[dict[str, Any]] = []
    for (repository, friction_type), events in sorted(grouped.items()):
        refs = sorted({event["evidence_ref"] for event in events})
        if len(refs) < minimum_recurrence:
            continue

        related_lessons = [
            lesson
            for lesson in normalized_memory["entries"]
            if lesson["repository"] in {repository, "jweter/project-orchestrator"}
            and any(token in (lesson["symptom"] + " " + lesson["root_cause"] + " " + lesson["regression_protection"]).lower()
                    for token in _friction_tokens(friction_type))
        ]

        mechanism, verification = _mechanism_for(friction_type)
        proposals.append(
            {
                "repository": repository,
                "friction_type": friction_type,
                "recurrence_count": len(refs),
                "evidence_refs": refs,
                "observed_bottleneck": _summarize(events),
                "related_verified_lessons": sorted(lesson["lesson_id"] for lesson in related_lessons),
                "proposed_mechanism": mechanism,
                "verification_plan": verification,
                "expected_capability_unlock": _capability_unlock(friction_type),
                "predicted_roi": "UNKNOWN",
                "predicted_time_saved": "UNKNOWN",
                "predicted_success_probability": "UNKNOWN",
                "safety_boundaries": [
                    "normal branch -> exact-head preflight -> PR -> fresh CI promotion remains mandatory",
                    "never weaken tests, required checks, security, provenance, licensing, privacy, or Product Reality rules",
                    "never override product repository architecture or roadmap authority",
                    "no destructive or irreversible action is authorized by this proposal",
                    "measured evidence is advisory; UNKNOWN remains UNKNOWN",
                ],
            }
        )

    metrics = _measured_metric_summary(normalized_store)
    return {
        "schema_version": 1,
        "minimum_recurrence": minimum_recurrence,
        "verified_friction_events_considered": sum(len(v) for v in grouped.values()),
        "proposal_count": len(proposals),
        "proposals": proposals,
        "measured_portfolio_context": metrics,
        "execution_authority": "PROPOSAL_ONLY",
        "next_gate": "select only authorized reversible proposals and implement them through normal governed engineering",
    }


def _friction_tokens(friction_type: str) -> tuple[str, ...]:
    mapping = {
        "repeat_ci_failure": ("ci", "failure", "regression"),
        "repeat_preflight_failure": ("preflight", "format", "lint"),
        "branch_reconciliation_friction": ("branch", "reconcile", "stale"),
        "stale_branch_accumulation": ("branch", "stale", "cleanup"),
        "manual_intervention": ("manual", "intervention", "jeremy"),
        "product_reality_false_freeze": ("product reality", "freeze", "blocked"),
        "evidence_gap": ("evidence", "unknown", "measurement"),
        "orchestration_control_failure": ("orchestration", "control", "scheduler"),
    }
    return mapping[friction_type]


def _mechanism_for(friction_type: str) -> tuple[str, list[str]]:
    mechanisms = {
        "repeat_ci_failure": (
            "promote the recurring verified failure class into earlier repository-native regression prevention",
            ["reproduce the failure class", "add earlier deterministic detection/protection", "prove targeted regression coverage", "run full exact-head preflight and fresh PR CI"],
        ),
        "repeat_preflight_failure": (
            "move deterministic formatting/lint/preflight prevention earlier in the development loop",
            ["add or tighten repository-approved pre-edit/pre-commit prevention", "prove the known failure is caught before PR creation", "run full exact-head preflight and fresh PR CI"],
        ),
        "branch_reconciliation_friction": (
            "extend automated stale-branch modernization using the existing reconciliation and lifecycle planners",
            ["exercise representative diverged branches in dry-run classification", "verify no published history rewrite", "prove recovered work reaches exact-head preflight"],
        ),
        "stale_branch_accumulation": (
            "automate evidence-safe branch lifecycle auditing and cleanup candidate generation",
            ["classify all non-default branches", "prove deletion candidates have no open PR or unique work", "revalidate live refs immediately before any deletion"],
        ),
        "manual_intervention": (
            "identify the narrow repeatable human step and replace only the reversible deterministic portion with automation",
            ["document current human decision boundary", "automate only the deterministic subset", "prove reserved decisions still require Jeremy"],
        ),
        "product_reality_false_freeze": (
            "strengthen lane-scoped Product Reality dependency classification",
            ["construct dependent and independent lane fixtures", "prove only dependent work parks", "prove parallel-safe work remains eligible"],
        ),
        "evidence_gap": (
            "add a trustworthy evidence source or explicitly preserve UNKNOWN when no deterministic source exists",
            ["define provenance for the new evidence", "validate schema and freshness", "prove missing evidence remains UNKNOWN rather than zero/pass"],
        ),
        "orchestration_control_failure": (
            "repair the control-plane invariant or scheduler policy that allowed the recurring orchestration defect",
            ["reproduce the control failure", "add a fail-closed policy/test", "prove normal product execution remains unblocked", "run exact-head preflight and fresh PR CI"],
        ),
    }
    return mechanisms[friction_type]


def _capability_unlock(friction_type: str) -> str:
    return {
        "repeat_ci_failure": "fewer repeated repair cycles for the same known failure class",
        "repeat_preflight_failure": "earlier prevention before PR creation",
        "branch_reconciliation_friction": "more stale engineering capital recovered safely",
        "stale_branch_accumulation": "cleaner branch inventory without deleting valuable work",
        "manual_intervention": "lower routine human burden while preserving reserved decisions",
        "product_reality_false_freeze": "higher independent-project liveness during human acceptance waits",
        "evidence_gap": "more trustworthy automation decisions with less ambiguous state",
        "orchestration_control_failure": "higher scheduler reliability and throughput",
    }[friction_type]


def _summarize(events: list[dict[str, Any]]) -> str:
    counts = Counter(event["summary"].strip() for event in events)
    return "; ".join(f"{summary} ({count}x)" for summary, count in sorted(counts.items()))


def _measured_metric_summary(store: dict[str, Any]) -> dict[str, Any]:
    windows = store["windows"]
    measured = 0
    unknown = 0
    insufficient = 0
    for window in windows:
        for metric in window["metrics"].values():
            if metric["status"] == "MEASURED":
                measured += 1
            elif metric["status"] == "UNKNOWN":
                unknown += 1
            else:
                insufficient += 1
    return {
        "window_count": len(windows),
        "measured_metric_entries": measured,
        "unknown_metric_entries": unknown,
        "insufficient_metric_entries": insufficient,
    }
