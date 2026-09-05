"""Deterministic Reinvestment Controller.

Answers, from measured evidence only: "given everything the portfolio has
learned, what engineering investment should we make next to most improve
future portfolio development?" It is a recommendation layer over
``project_orchestrator.timeseries.build_reinvestment_snapshot`` — it never
merges PRs, bypasses CI, deletes anything, or overrides product-repository
authority. Every recommendation in this v1 slice requires Jeremy's approval
before any action is taken on it; nothing here executes automatically.

Design notes on the scoring model (see docs/ARCHITECTURE.md for the full
writeup):

- A candidate's ``priority_score`` is an unbounded, unitless, ordinal
  ranking number built only from bounded per-component transforms of real
  measured evidence. It is deliberately not expressed as predicted hours
  saved, ROI, or probability — this module never fabricates an improvement
  estimate, only ranks real measurements against a declared, fixed policy.
- A scoring component is included only when real evidence backs it. When
  the underlying metric is UNKNOWN or INSUFFICIENT_DATA, the component is
  *omitted* from the score (never coerced to 0) and ``confidence`` drops
  instead, per the explicit requirement that missing evidence must reduce
  confidence rather than silently score as "no impact".
- ``implementation_cost_complexity`` is always UNKNOWN in this slice: no
  deterministic cost model exists yet. That absence is itself reported as a
  standing evidence gap rather than hidden.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .timeseries import ALLOWED_METRIC_KEYS, build_reinvestment_snapshot

# --- fixed policy (weights/scales), not measured evidence -----------------

MIN_WINDOWS_FOR_CONFIDENCE = 5
MIN_CONFIDENCE_FOR_SELECTION = 0.5
COMPONENT_CAP = 10.0
SECONDS_PER_SCORE_POINT = 3600.0  # 1 point per hour, capped at COMPONENT_CAP
COUNT_SCORE_PER_UNIT = 2.0  # 1 occurrence = 2 points, capped at COMPONENT_CAP
SAMPLE_REFERENCE = 5.0  # sample_count reaching this saturates recurrence score

COMPONENT_NAMES = (
    "cycle_time_reduction",
    "recurrence_frequency",
    "failure_severity",
    "affected_scope",
    "blocked_development_impact",
    "product_reality_impact",
    "human_intervention_burden",
    "implementation_cost_complexity",
)

CATEGORIES: dict[str, str] = {
    "recurring_ci_failure_prevention": "Prevent CI failures that keep recurring in a repository.",
    "preflight_tooling_improvement": "Extend preflight to catch what currently only fails in CI.",
    "failure_repair_automation": "Automate/accelerate repair of failed PR CI.",
    "cross_repo_compatibility_improvement": "Reduce cross-repository/dependency-family incidents.",
    "product_reality_debt_reduction": "Resolve accumulating Product Reality debt.",
    "roadmap_capability_delivery": "Continue delivering authorized roadmap capability.",
    "orchestration_control_plane_improvement": "Improve the orchestrator's own evidence/control infrastructure.",
    "evidence_observability_gap": "Close a gap where the portfolio cannot currently measure itself.",
    "jeremy_required_intervention_reduction": "Reduce how often Jeremy must intervene manually.",
}

SAFETY_BOUNDARIES = (
    "no_automatic_merge_of_product_prs",
    "no_bypassing_required_ci",
    "no_destructive_or_irreversible_action_without_jeremy_approval",
    "no_overriding_product_repository_architecture_or_product_reality_policy",
    "no_treating_unknown_evidence_as_known",
    "recommendation_only_in_this_slice_no_automatic_execution",
)


class ReinvestmentError(ValueError):
    """Raised when reinvestment planning input violates the deterministic contract."""


# --- component helpers ------------------------------------------------


def _seconds_component(metric: dict[str, Any] | None) -> dict[str, Any]:
    if metric is None or metric.get("status") != "MEASURED":
        return {"status": (metric or {}).get("status", "UNKNOWN")}
    score = min(COMPONENT_CAP, metric["value"] / SECONDS_PER_SCORE_POINT * COMPONENT_CAP)
    return {"status": "MEASURED", "value": score, "evidence_refs": metric["evidence_refs"]}


def _count_component(metric: dict[str, Any] | None) -> dict[str, Any]:
    if metric is None or metric.get("status") != "MEASURED":
        return {"status": (metric or {}).get("status", "UNKNOWN")}
    score = min(COMPONENT_CAP, metric["value"] * COUNT_SCORE_PER_UNIT)
    return {"status": "MEASURED", "value": score, "evidence_refs": metric["evidence_refs"]}


def _fixed_component(value: float, evidence_refs: list[str]) -> dict[str, Any]:
    return {
        "status": "MEASURED",
        "value": min(COMPONENT_CAP, value),
        "evidence_refs": sorted(set(evidence_refs)),
    }


def _unknown_component() -> dict[str, Any]:
    return {"status": "UNKNOWN"}


def _score_and_confidence(components: dict[str, dict[str, Any]]) -> tuple[float, float]:
    measured = [c for c in components.values() if c["status"] == "MEASURED"]
    raw_score = sum(c["value"] for c in measured)
    confidence = len(measured) / len(COMPONENT_NAMES)
    return raw_score, confidence


def _candidate_id(category: str, scope_type: str, scope: str, evidence_refs: list[str]) -> str:
    stable = {
        "category": category,
        "scope_type": scope_type,
        "scope": scope,
        "evidence_refs": sorted(set(evidence_refs)),
    }
    encoded = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def _build_candidate(
    category: str,
    scope_type: str,
    scope: str,
    components: dict[str, dict[str, Any]],
    reasons: list[str],
) -> dict[str, Any]:
    for name in COMPONENT_NAMES:
        if name not in components:
            components[name] = _unknown_component()
    raw_score, confidence = _score_and_confidence(components)
    evidence_refs = sorted(
        {ref for c in components.values() if c["status"] == "MEASURED" for ref in c["evidence_refs"]}
    )
    return {
        "candidate_id": _candidate_id(category, scope_type, scope, evidence_refs),
        "category": category,
        "scope_type": scope_type,
        "scope": scope,
        "priority_score": raw_score,
        "confidence": confidence,
        "components": components,
        "evidence_refs": evidence_refs,
        "reasons": reasons,
    }


def _scope_dict(snapshot: dict[str, Any], section: str, scope: str) -> dict[str, Any]:
    # "portfolio" is a single aggregate object, not a dict keyed by scope.
    if section == "portfolio":
        return snapshot.get("portfolio", {})
    return snapshot.get(section, {}).get(scope, {})


def _metric(snapshot: dict[str, Any], section: str, scope: str, key: str) -> dict[str, Any] | None:
    return _scope_dict(snapshot, section, scope).get("metrics", {}).get(key)


def _window_ids(snapshot: dict[str, Any], section: str, scope: str) -> list[str]:
    return _scope_dict(snapshot, section, scope).get("window_ids", [])


# --- per-category candidate generation -------------------------------


def _generate_recurring_ci_failure_candidates(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = []
    repos_with_repeats: list[str] = []
    for repo in sorted(snapshot.get("by_repository", {})):
        repeats = _metric(snapshot, "by_repository", repo, "ci_repeat_failure_count")
        if repeats is None or repeats["status"] != "MEASURED" or repeats["value"] <= 0:
            continue
        repos_with_repeats.append(repo)
        components = {
            "failure_severity": _count_component(repeats),
            "recurrence_frequency": _count_component(
                _metric(snapshot, "by_repository", repo, "ci_failure_count")
            ),
            "affected_scope": _fixed_component(2.0, repeats["evidence_refs"]),
            "cycle_time_reduction": _seconds_component(
                _metric(snapshot, "by_repository", repo, "pr_creation_to_green_seconds")
            ),
            "blocked_development_impact": _seconds_component(
                _metric(snapshot, "by_repository", repo, "blocked_waiting_seconds")
            ),
            "human_intervention_burden": _count_component(
                _metric(snapshot, "by_repository", repo, "jeremy_required_intervention_count")
            ),
        }
        candidates.append(
            _build_candidate(
                "recurring_ci_failure_prevention",
                "repository",
                repo,
                components,
                [f"{repo} shows {repeats['value']:g} recurring (repeat) CI failures across measured evidence"],
            )
        )
    if len(repos_with_repeats) >= 2:
        refs = sorted(
            {
                ref
                for repo in repos_with_repeats
                for ref in _metric(snapshot, "by_repository", repo, "ci_repeat_failure_count")["evidence_refs"]
            }
        )
        components = {
            "affected_scope": _fixed_component(float(len(repos_with_repeats)) * COUNT_SCORE_PER_UNIT, refs),
            "recurrence_frequency": _fixed_component(COMPONENT_CAP, refs),
        }
        candidates.append(
            _build_candidate(
                "recurring_ci_failure_prevention",
                "portfolio",
                "portfolio",
                components,
                [
                    "recurring CI failures affect multiple repositories: "
                    + ", ".join(repos_with_repeats)
                ],
            )
        )
    return candidates


def _generate_preflight_tooling_candidates(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = []
    for repo in sorted(snapshot.get("by_repository", {})):
        prevented = _metric(snapshot, "by_repository", repo, "preflight_failures_prevented_count")
        failures = _metric(snapshot, "by_repository", repo, "ci_failure_count")
        if (
            prevented is not None
            and prevented["status"] == "MEASURED"
            and prevented["value"] == 0
            and failures is not None
            and failures["status"] == "MEASURED"
            and failures["value"] > 0
        ):
            components = {
                "failure_severity": _count_component(failures),
                "recurrence_frequency": _count_component(failures),
                "affected_scope": _fixed_component(2.0, failures["evidence_refs"]),
            }
            candidates.append(
                _build_candidate(
                    "preflight_tooling_improvement",
                    "repository",
                    repo,
                    components,
                    [
                        f"{repo} recorded {failures['value']:g} CI failures while preflight prevented 0, "
                        "suggesting a preflight coverage gap"
                    ],
                )
            )
    return candidates


def _generate_failure_repair_candidates(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = []
    for repo in sorted(snapshot.get("by_repository", {})):
        repair = _metric(snapshot, "by_repository", repo, "failure_to_repair_seconds")
        if repair is None or repair["status"] != "MEASURED" or repair["value"] <= 0:
            continue
        components = {
            "cycle_time_reduction": _seconds_component(repair),
            "failure_severity": _seconds_component(repair),
            "recurrence_frequency": _count_component(
                _metric(snapshot, "by_repository", repo, "ci_failure_count")
            ),
            "affected_scope": _fixed_component(2.0, repair["evidence_refs"]),
            "human_intervention_burden": _count_component(
                _metric(snapshot, "by_repository", repo, "jeremy_required_intervention_count")
            ),
        }
        candidates.append(
            _build_candidate(
                "failure_repair_automation",
                "repository",
                repo,
                components,
                [f"{repo} measured failure-to-repair time of {repair['value']:g}s"],
            )
        )
    return candidates


def _generate_cross_repo_candidates(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = []
    for project in sorted(snapshot.get("by_project", {})):
        incidents = _metric(snapshot, "by_project", project, "cross_repo_incident_count")
        if incidents is None or incidents["status"] != "MEASURED" or incidents["value"] <= 0:
            continue
        components = {
            "failure_severity": _count_component(incidents),
            "recurrence_frequency": _count_component(incidents),
            "affected_scope": _fixed_component(COMPONENT_CAP, incidents["evidence_refs"]),
        }
        candidates.append(
            _build_candidate(
                "cross_repo_compatibility_improvement",
                "project",
                project,
                components,
                [f"{project} measured {incidents['value']:g} cross-repository compatibility incidents"],
            )
        )
    return candidates


def _generate_product_reality_candidates(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = []
    for repo in sorted(snapshot.get("by_repository", {})):
        created = _metric(snapshot, "by_repository", repo, "product_reality_debt_created_count")
        resolved = _metric(snapshot, "by_repository", repo, "product_reality_debt_resolved_count")
        if created is None or created["status"] != "MEASURED":
            continue
        resolved_value = resolved["value"] if resolved is not None and resolved["status"] == "MEASURED" else 0.0
        net = created["value"] - resolved_value
        if net <= 0:
            continue
        components = {
            "product_reality_impact": _count_component({"status": "MEASURED", "value": net, "evidence_refs": created["evidence_refs"]}),
            "recurrence_frequency": _count_component(created),
            "affected_scope": _fixed_component(2.0, created["evidence_refs"]),
        }
        candidates.append(
            _build_candidate(
                "product_reality_debt_reduction",
                "repository",
                repo,
                components,
                [f"{repo} shows net Product Reality debt of {net:g} (created minus resolved)"],
            )
        )
    return candidates


def _generate_roadmap_candidate(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    slices = snapshot.get("portfolio", {}).get("metrics", {}).get("roadmap_slices_completed_count")
    if slices is None or slices["status"] != "MEASURED":
        return []
    components = {
        "recurrence_frequency": _count_component(slices),
        "affected_scope": _fixed_component(COUNT_SCORE_PER_UNIT, slices["evidence_refs"]),
        # a small fixed baseline so continued roadmap delivery only wins
        # when no repair/gap category has real measured signal
        "cycle_time_reduction": _fixed_component(1.0, slices["evidence_refs"]),
    }
    return [
        _build_candidate(
            "roadmap_capability_delivery",
            "portfolio",
            "portfolio",
            components,
            [f"portfolio measured {slices['value']:g} roadmap slices completed; no higher-priority repair signal outranks continuing delivery"],
        )
    ]


def _generate_jeremy_intervention_candidate(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    jeremy = snapshot.get("portfolio", {}).get("metrics", {}).get("jeremy_required_intervention_count")
    autonomous = snapshot.get("portfolio", {}).get("metrics", {}).get("autonomous_intervention_count")
    if jeremy is None or jeremy["status"] != "MEASURED" or jeremy["value"] <= 0:
        return []
    components = {
        "human_intervention_burden": _count_component(jeremy),
        "recurrence_frequency": _count_component(jeremy),
        "affected_scope": _fixed_component(COMPONENT_CAP, jeremy["evidence_refs"]),
    }
    reasons = [f"portfolio measured {jeremy['value']:g} Jeremy-required interventions"]
    if autonomous is not None and autonomous["status"] == "MEASURED":
        reasons.append(f"versus {autonomous['value']:g} autonomous interventions")
    return [
        _build_candidate(
            "jeremy_required_intervention_reduction",
            "portfolio",
            "portfolio",
            components,
            reasons,
        )
    ]


def _generate_orchestration_control_plane_candidate(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    window_count = snapshot.get("portfolio", {}).get("window_count", 0)
    if window_count >= MIN_WINDOWS_FOR_CONFIDENCE:
        return []
    window_ids = _window_ids(snapshot, "portfolio", "portfolio")
    components = {
        "affected_scope": _fixed_component(COMPONENT_CAP, window_ids),
        "recurrence_frequency": _fixed_component(
            min(COMPONENT_CAP, (MIN_WINDOWS_FOR_CONFIDENCE - window_count) * COUNT_SCORE_PER_UNIT), window_ids
        ),
    }
    return [
        _build_candidate(
            "orchestration_control_plane_improvement",
            "portfolio",
            "portfolio",
            components,
            [
                f"only {window_count} evidence window(s) recorded (below the "
                f"{MIN_WINDOWS_FOR_CONFIDENCE} needed for confident measurement); "
                "no automatic collector yet populates the compounding time-series store"
            ],
        )
    ]


def _generate_evidence_observability_candidate(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    portfolio_metrics = snapshot.get("portfolio", {}).get("metrics", {})
    gappy = sorted(
        key
        for key in ALLOWED_METRIC_KEYS
        if portfolio_metrics.get(key, {}).get("status") in {"UNKNOWN", "INSUFFICIENT_DATA", None}
    )
    if not gappy:
        return []
    window_ids = _window_ids(snapshot, "portfolio", "portfolio")
    components = {
        "recurrence_frequency": _fixed_component(
            min(COMPONENT_CAP, len(gappy) / len(ALLOWED_METRIC_KEYS) * COMPONENT_CAP), window_ids
        ),
        "affected_scope": _fixed_component(min(COMPONENT_CAP, len(gappy)), window_ids),
    }
    return [
        _build_candidate(
            "evidence_observability_gap",
            "portfolio",
            "portfolio",
            components,
            [f"portfolio-level metrics with no MEASURED evidence: {', '.join(gappy)}"],
        )
    ]


_GENERATORS = (
    _generate_recurring_ci_failure_candidates,
    _generate_preflight_tooling_candidates,
    _generate_failure_repair_candidates,
    _generate_cross_repo_candidates,
    _generate_product_reality_candidates,
    _generate_roadmap_candidate,
    _generate_jeremy_intervention_candidate,
    _generate_orchestration_control_plane_candidate,
    _generate_evidence_observability_candidate,
)


def generate_candidates(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for generator in _GENERATORS:
        candidates.extend(generator(snapshot))
    candidates.sort(
        key=lambda c: (-c["priority_score"], -c["confidence"], c["candidate_id"])
    )
    return candidates


def _evidence_gaps(snapshot: dict[str, Any], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    portfolio_metrics = snapshot.get("portfolio", {}).get("metrics", {})
    for key in sorted(ALLOWED_METRIC_KEYS):
        status = portfolio_metrics.get(key, {}).get("status", "UNKNOWN")
        if status in {"UNKNOWN", "INSUFFICIENT_DATA"}:
            gaps.append(
                {
                    "gap": f"metric:{key}",
                    "status": status,
                    "note": "no MEASURED evidence for this metric at portfolio scope",
                }
            )
    gaps.append(
        {
            "gap": "implementation_cost_complexity",
            "status": "UNKNOWN",
            "note": "no deterministic implementation-cost/complexity model exists yet for any candidate",
        }
    )
    if snapshot.get("portfolio", {}).get("window_count", 0) < MIN_WINDOWS_FOR_CONFIDENCE:
        gaps.append(
            {
                "gap": "evidence_volume",
                "status": "INSUFFICIENT_DATA",
                "note": (
                    f"fewer than {MIN_WINDOWS_FOR_CONFIDENCE} recorded evidence windows; "
                    "confidence in any recommendation is bounded until more evidence accumulates"
                ),
            }
        )
    return gaps


def build_plan(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Produce a deterministic reinvestment recommendation from a reinvestment snapshot.

    ``snapshot`` must be the output of
    :func:`project_orchestrator.timeseries.build_reinvestment_snapshot`.
    """
    if not isinstance(snapshot, dict) or "portfolio" not in snapshot:
        raise ReinvestmentError("snapshot must be a build_reinvestment_snapshot() output")

    candidates = generate_candidates(snapshot)

    selected = None
    rejected_or_deferred: list[dict[str, Any]] = []
    for candidate in candidates:
        if selected is None and candidate["confidence"] >= MIN_CONFIDENCE_FOR_SELECTION:
            selected = candidate
            continue
        if candidate is selected:
            continue
        reason = (
            "confidence below minimum threshold for selection"
            if candidate["confidence"] < MIN_CONFIDENCE_FOR_SELECTION
            else "lower priority_score than the selected recommendation"
        )
        rejected_or_deferred.append({"candidate_id": candidate["candidate_id"], "reason": reason})

    return {
        "schema_version": 1,
        "generated_from_window_count": snapshot.get("generated_from_window_count", 0),
        "since": snapshot.get("since"),
        "categories": CATEGORIES,
        "candidates": candidates,
        "selected": selected,
        "rejected_or_deferred": rejected_or_deferred,
        # Always true in this slice: recommendation only, never auto-executed.
        "requires_jeremy_approval": True,
        "evidence_gaps": _evidence_gaps(snapshot, candidates),
        "safety_boundaries": list(SAFETY_BOUNDARIES),
    }


def plan_from_store(store: dict[str, Any], *, since: str | None = None) -> dict[str, Any]:
    snapshot = build_reinvestment_snapshot(store, since=since)
    return build_plan(snapshot)
