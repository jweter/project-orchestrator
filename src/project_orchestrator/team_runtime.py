from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any

PROJECT_IDS = ("knowledge-engine", "rocksmith", "everward", "voicestudio")
IMPLEMENTATION_ROLES = ("implementation-primary", "implementation-secondary")
REVIEW_ROLES = ("qa-review",)
TERMINAL_STATES = {"MERGED", "PASS", "REJECTED", "CANCELLED", "ABANDONED"}


def _stable_id(prefix: str, payload: dict[str, Any]) -> str:
    material = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"{prefix}-{hashlib.sha256(material).hexdigest()[:16]}"


def _overlap(left: list[str], right: list[str]) -> bool:
    for a in left:
        for b in right:
            if a == b or a.startswith(b.rstrip("/") + "/") or b.startswith(a.rstrip("/") + "/"):
                return True
    return False


def build_work_contract(candidate: dict[str, Any], *, role: str) -> dict[str, Any]:
    """Create a bounded, replay-safe specialist contract from an authorized candidate."""
    required = ("project_id", "repository", "base_sha", "branch", "title", "paths")
    missing = [key for key in required if not candidate.get(key)]
    if missing:
        raise ValueError("candidate missing contract fields: " + ", ".join(missing))
    if candidate["project_id"] not in PROJECT_IDS:
        raise ValueError("candidate is outside the canonical product portfolio")
    if role not in (*IMPLEMENTATION_ROLES, *REVIEW_ROLES):
        raise ValueError("unsupported team role")

    payload = {
        "project_id": candidate["project_id"],
        "repository": candidate["repository"],
        "base_sha": candidate["base_sha"],
        "branch": candidate["branch"],
        "title": candidate["title"],
        "paths": sorted(set(candidate["paths"])),
        "role": role,
    }
    return {
        "schema_version": 1,
        "contract_id": _stable_id("contract", payload),
        **payload,
        "objective": candidate.get("objective") or candidate["title"],
        "acceptance": list(candidate.get("acceptance", [])),
        "dependencies": sorted(set(candidate.get("dependencies", []))),
        "verification": list(candidate.get("verification", [])),
        "product_reality_dependency": candidate.get("product_reality_dependency"),
        "authority": {
            "edit_authorized_paths": role in IMPLEMENTATION_ROLES,
            "add_regression_tests_within_authorized_paths": role in IMPLEMENTATION_ROLES,
            "review_and_reject": role in REVIEW_ROLES,
            "merge": False,
            "weaken_gates": False,
            "infer_product_reality": False,
        },
    }


def build_ephemeral_leases(
    contracts: list[dict[str, Any]], live_active_work: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Project conflict-free leases without creating a competing ownership database."""
    leases: list[dict[str, Any]] = []
    occupied = [
        {
            "repository": item.get("repository"),
            "branch": item.get("branch"),
            "paths": item.get("changed_files", item.get("paths", [])),
        }
        for item in live_active_work
        if isinstance(item, dict)
    ]

    for contract in contracts:
        conflict = None
        for item in [*occupied, *leases]:
            if item.get("repository") != contract["repository"]:
                continue
            if item.get("branch") == contract["branch"]:
                continue
            if _overlap(contract["paths"], item.get("paths", [])):
                conflict = item.get("branch") or "live-active-work"
                break
        state = "LEASED" if conflict is None else "CONFLICT"
        lease = {
            "lease_id": _stable_id("lease", {"contract_id": contract["contract_id"]}),
            "contract_id": contract["contract_id"],
            "project_id": contract["project_id"],
            "repository": contract["repository"],
            "branch": contract["branch"],
            "base_sha": contract["base_sha"],
            "paths": contract["paths"],
            "state": state,
            "reason": "conflict-free ephemeral projection" if conflict is None else f"path overlap with {conflict}",
            "persistent_ownership": False,
        }
        leases.append(lease)
        if state == "LEASED":
            occupied.append(lease)
    return leases


def build_team_plan(
    candidates: list[dict[str, Any]],
    *,
    live_active_work: list[dict[str, Any]] | None = None,
    max_implementation_workers_per_project: int = 2,
) -> dict[str, Any]:
    """Build a work-conserving development-team plan without weakening Foreman authority."""
    if max_implementation_workers_per_project < 1:
        raise ValueError("at least one implementation worker is required")

    selected: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for candidate in sorted(
        candidates,
        key=lambda item: (
            item.get("priority", 999),
            item.get("project_id", ""),
            item.get("repository", ""),
            item.get("title", ""),
        ),
    ):
        project = candidate.get("project_id")
        if project not in PROJECT_IDS or candidate.get("eligibility_state", "ELIGIBLE") != "ELIGIBLE":
            continue
        if counts[project] >= max_implementation_workers_per_project:
            continue
        selected.append(candidate)
        counts[project] += 1

    contracts = [
        build_work_contract(
            candidate,
            role=IMPLEMENTATION_ROLES[index % len(IMPLEMENTATION_ROLES)],
        )
        for project in PROJECT_IDS
        for index, candidate in enumerate([x for x in selected if x["project_id"] == project])
    ]
    leases = build_ephemeral_leases(contracts, live_active_work or [])
    lease_by_contract = {lease["contract_id"]: lease for lease in leases}
    dispatch = [
        contract
        for contract in contracts
        if lease_by_contract[contract["contract_id"]]["state"] == "LEASED"
    ]

    return {
        "schema_version": 1,
        "runtime": "DevelopmentTeamRuntime",
        "dispatch": dispatch,
        "leases": leases,
        "ready_queue_depth": Counter(x["project_id"] for x in candidates if x.get("eligibility_state", "ELIGIBLE") == "ELIGIBLE"),
        "roles": {
            "team_director": "Foreman",
            "implementation_workers": list(IMPLEMENTATION_ROLES),
            "independent_review": list(REVIEW_ROLES),
            "failure_repair": "existing deterministic repair / Pit Crew path",
            "merge_authority": "existing central merge authority only",
        },
        "safety_boundaries": [
            "GitHub remains ownership authority; leases are ephemeral projections only",
            "workers edit only contract-authorized paths",
            "implementation workers cannot approve or merge their own work",
            "QA/review cannot infer Product Reality",
            "repository-local preflight and CI remain authoritative",
            "worker count is bounded and may be reduced when measured rework or collision cost rises",
        ],
    }


def build_qa_handoff(
    implementation_result: dict[str, Any],
    *,
    adversarial_checks: list[str] | None = None,
) -> dict[str, Any]:
    """Turn a completed implementation result into an independent adversarial QA contract."""
    required = ("contract_id", "repository", "branch", "exact_head_sha", "changed_paths")
    missing = [key for key in required if key not in implementation_result]
    if missing:
        raise ValueError("implementation result missing QA fields: " + ", ".join(missing))
    if implementation_result.get("status") != "PASS":
        return {
            "state": "REPAIR_REQUIRED",
            "source_contract_id": implementation_result["contract_id"],
            "reason": "implementation has not passed its own verification",
        }
    payload = {
        "source_contract_id": implementation_result["contract_id"],
        "repository": implementation_result["repository"],
        "branch": implementation_result["branch"],
        "exact_head_sha": implementation_result["exact_head_sha"],
    }
    return {
        "schema_version": 1,
        "qa_contract_id": _stable_id("qa", payload),
        **payload,
        "role": "qa-review",
        "state": "READY",
        "changed_paths": sorted(set(implementation_result["changed_paths"])),
        "objective": "Find a reproducible counterexample or regression before promotion",
        "adversarial_checks": adversarial_checks
        or [
            "boundary_and_malformed_input",
            "state_transition_regression",
            "stale_or_duplicate_state",
            "architecture_and_authority_drift",
            "test_gap_or_false_positive_success",
        ],
        "authority": {"edit_product_code": False, "approve_for_merge": False, "reject_to_repair": True},
    }


def measure_team_flow(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Project measured team-flow counters; unavailable durations remain UNKNOWN."""
    counts = Counter(str(event.get("kind", "UNKNOWN")).upper() for event in events if isinstance(event, dict))
    verified = counts["MERGED"] + counts["PRODUCT_REALITY_VERIFIED"]
    return {
        "schema_version": 1,
        "verified_advancement_events": verified,
        "first_pass_green": counts["FIRST_PASS_GREEN"],
        "repair_events": counts["REPAIR"],
        "collision_events": counts["COLLISION"],
        "idle_capacity_events": counts["IDLE_CAPACITY"],
        "qa_rejections": counts["QA_REJECTED"],
        "cycle_time": "UNKNOWN",
        "recommendation": (
            "REDUCE_CONCURRENCY"
            if counts["COLLISION"] + counts["QA_REJECTED"] > verified and (counts["COLLISION"] + counts["QA_REJECTED"]) > 0
            else "HOLD_AND_MEASURE"
        ),
    }
