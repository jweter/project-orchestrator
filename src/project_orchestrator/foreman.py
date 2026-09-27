from __future__ import annotations

from collections import Counter
from typing import Any

from .specialist_lanes import build_lane_plan
from .jev_shadow import evaluate_shadow
from .steward import assess_machine_health, govern_dispatch

FOREMAN_NAME = "Foreman"
PROJECT_IDS = ("knowledge-engine", "rocksmith", "everward", "voicestudio")
TERMINAL_STATES = {
    "PASS",
    "FAIL",
    "NO_OP",
    "COMPLETED",
    "MERGED",
    "CANCELLED",
    "ABANDONED",
    "REJECTED",
}
ASYNC_STATES = {
    "CI_PENDING",
    "PREFLIGHT_RUNNING",
    "PREFLIGHT_PENDING",
    "PRODUCT_REALITY_PENDING",
    "WINDOWS_PENDING",
    "OLLAMA_PENDING",
    "UNREAL_PENDING",
    "REVIEW_BLOCKED",
    "PR_BLOCKED",
}


def _repo_projects(inventory: dict[str, Any]) -> dict[str, str]:
    return {
        repo["name"]: project["id"]
        for project in inventory.get("projects", [])
        for repo in project.get("repositories", [])
    }


def _project_repositories(inventory: dict[str, Any]) -> dict[str, list[str]]:
    return {
        project["id"]: [repo["name"] for repo in project.get("repositories", [])]
        for project in inventory.get("projects", [])
    }


def _state(record: dict[str, Any]) -> str:
    raw = (
        record.get("state") or record.get("status") or record.get("stage") or "UNKNOWN"
    )
    return str(raw).upper()


def _sanitize_execution_record(
    record: dict[str, Any], project_id: str
) -> dict[str, Any]:
    keys = (
        "grant_id",
        "task_key",
        "repository",
        "state",
        "status",
        "stage",
        "branch",
        "head_sha",
        "base_sha",
        "updated_at_utc",
        "last_heartbeat_at",
        "result_status",
    )
    return {
        "project_id": project_id,
        **{key: record.get(key) for key in keys if key in record},
    }


def compact_execution_records(
    inventory: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    archive_limit: int = 25,
) -> dict[str, Any]:
    """Separate scheduling-relevant execution state from bounded historical evidence."""
    repo_projects = _repo_projects(inventory)
    buckets: dict[str, dict[str, list[dict[str, Any]]]] = {
        project: {
            "active": [],
            "waiting": [],
            "quarantined_actionable": [],
            "archived": [],
        }
        for project in PROJECT_IDS
    }
    unknown: list[dict[str, Any]] = []

    for record in records:
        if not isinstance(record, dict):
            continue
        project = record.get("project_id") or repo_projects.get(
            record.get("repository")
        )
        if project not in buckets:
            unknown.append(record)
            continue
        item = _sanitize_execution_record(record, str(project))
        state = _state(record)
        if state in ASYNC_STATES:
            buckets[str(project)]["waiting"].append(item)
        elif state == "QUARANTINED" and record.get("actionable") is True:
            buckets[str(project)]["quarantined_actionable"].append(item)
        elif state in TERMINAL_STATES or state == "QUARANTINED":
            buckets[str(project)]["archived"].append(item)
        else:
            buckets[str(project)]["active"].append(item)
    archive_summary: dict[str, Any] = {}
    for project, values in buckets.items():
        archived = values["archived"]
        counts = Counter(_state(item) for item in archived)
        values["archived"] = archived[-archive_limit:]
        archive_summary[project] = {
            "total": sum(counts.values()),
            "by_state": dict(sorted(counts.items())),
            "retained_recent": len(values["archived"]),
        }

    return {
        "schema_version": 1,
        "projects": buckets,
        "archive_summary": archive_summary,
        "unknown_record_count": len(unknown),
        "scheduling_rule": (
            "only active, asynchronous waits, and explicitly actionable quarantines "
            "participate in scheduling"
        ),
    }


def _live_ownership(
    inventory: dict[str, Any], live_snapshot: dict[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    repo_projects = _repo_projects(inventory)
    result = {project: [] for project in PROJECT_IDS}
    for repo in live_snapshot.get("repositories", []):
        repository = repo.get("repository")
        project = repo_projects.get(repository)
        if project not in result:
            continue
        for work in repo.get("active_work", []):
            if isinstance(work, dict):
                result[project].append({"repository": repository, **work})
    return result


def _shadow_candidate(lane: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": lane["lane_id"], "project_id": lane["project_id"],
        "repository": lane["repository"], "title": lane["title"],
        "priority": lane["priority"], "eligibility_state": "ELIGIBLE",
        "resource_class": lane["resource_class"],
        "continuation_kind": "BOUNDED_EXECUTION",
    }


def _harvest_jev_decisions(*, shadow_candidates: dict[str, list[dict[str, Any]]],
                           candidate_dispatch_queue: list[dict[str, Any]],
                           compacted: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Harvest bounded multi-option choices for shadow evaluation only."""
    harvested: dict[str, dict[str, Any]] = {}
    for project in PROJECT_IDS:
        options = shadow_candidates[project]
        if len(options) >= 2:
            harvested[f"{project}:next-slice"] = evaluate_shadow(
                state={"decision_id": f"foreman:{project}:next-slice",
                       "project_id": project, "current_gate": "NEXT_SLICE_SELECTION"},
                candidates=options, live=False)
        actionable = compacted["projects"][project]["quarantined_actionable"]
        if len(actionable) >= 2:
            repairs = [{
                "candidate_id": str(x.get("grant_id") or x.get("task_key") or f"{project}:repair:{i}"),
                "project_id": project, "repository": str(x.get("repository") or "unknown"),
                "title": f"Repair actionable quarantine {x.get('grant_id') or x.get('task_key') or i}",
                "priority": 1, "eligibility_state": "ELIGIBLE",
                "continuation_kind": "REPAIR_EXISTING",
            } for i, x in enumerate(actionable)]
            harvested[f"{project}:repair-selection"] = evaluate_shadow(
                state={"decision_id": f"foreman:{project}:repair-selection",
                       "project_id": project, "current_gate": "REPAIR_SELECTION",
                       "worker_result_class": "QUARANTINED_ACTIONABLE"},
                candidates=repairs, live=False)
    portfolio = [_shadow_candidate(lane) for lane in candidate_dispatch_queue]
    if len(portfolio) >= 2:
        harvested["portfolio:allocation"] = evaluate_shadow(
            state={"decision_id": "foreman:portfolio:allocation",
                   "project_id": "portfolio", "current_gate": "CROSS_REPO_ALLOCATION"},
            candidates=portfolio, live=False)
    return harvested


def build_foreman_plan(
    inventory: dict[str, Any],
    live_snapshot: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    execution_records: list[dict[str, Any]] | None = None,
    events: list[dict[str, Any]] | None = None,
    machine_health: dict[str, Any] | None = None,
    steward_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project live evidence into the canonical work-conserving portfolio lanes."""
    projects = _project_repositories(inventory)
    if set(projects) != set(PROJECT_IDS):
        raise ValueError(
            "Foreman requires exactly Knowledge Engine, Rocksmith, Everward, and VoiceStudio"
        )

    compacted = compact_execution_records(inventory, execution_records or [])
    live_ownership = _live_ownership(inventory, live_snapshot)
    reservations = build_lane_plan(inventory, live_snapshot, candidates)
    by_project = {project: [] for project in PROJECT_IDS}
    shadow_candidates = {project: [] for project in PROJECT_IDS}
    for candidate in candidates:
        project = candidate.get("project_id")
        if project not in shadow_candidates:
            continue
        isolated = build_lane_plan(inventory, live_snapshot, [candidate])["lanes"]
        if not isolated or isolated[0]["state"] != "RESERVED":
            continue
        lane = isolated[0]
        shadow_candidates[project].append(_shadow_candidate(lane))
    jev_shadow = {
        project: evaluate_shadow(
            state={
                "decision_id": f"foreman:{project}:candidate-ordering",
                "project_id": project,
                "current_gate": "FOREMAN_CANDIDATE_ORDERING",
            },
            candidates=shadow_candidates[project],
            live=False,
        )
        for project in PROJECT_IDS
    }
    for lane in reservations["lanes"]:
        project = lane.get("project_id")
        if project in by_project:
            by_project[project].append(lane)

    ledgers: list[dict[str, Any]] = []
    dispatch_queue: list[dict[str, Any]] = []
    rotated_waits: list[str] = []

    for project in PROJECT_IDS:
        projected = compacted["projects"][project]
        lane_candidates = by_project[project]
        selected = next(
            (lane for lane in lane_candidates if lane["state"] == "RESERVED"), None
        )
        active = [*live_ownership[project], *projected["active"]]
        waiting = projected["waiting"]
        actionable = projected["quarantined_actionable"]

        if selected is not None:
            state = "DISPATCH_READY"
            next_gate = "DISPATCH_BOUNDED_EXECUTION"
            dispatch_queue.append(selected)
            if waiting:
                rotated_waits.append(project)
        elif active:
            state = "ACTIVE_EXISTING"
            next_gate = "RECONCILE_OR_ADVANCE_EXISTING_WORK"
        elif actionable:
            state = "BLOCKED_REPAIR"
            next_gate = "REPAIR_ACTIONABLE_QUARANTINE"
        elif waiting:
            state = "WAITING_ASYNC"
            next_gate = "AWAIT_EVENT_WITHOUT_BLOCKING_OTHER_PRODUCTS"
        elif lane_candidates:
            state = "BLOCKED"
            next_gate = "RECONCILE_BLOCKED_CANDIDATES"
        else:
            state = "NO_SAFE_ELIGIBLE_WORK"
            next_gate = "REFRESH_AUTHORIZED_CANDIDATES"

        ledgers.append(
            {
                "project_id": project,
                "repositories": projects[project],
                "state": state,
                "next_gate": next_gate,
                "selected_lane": selected,
                "live_ownership": live_ownership[project],
                "active_execution": projected["active"],
                "asynchronous_waits": waiting,
                "actionable_quarantines": actionable,
                "candidate_states": [
                    {
                        "lane_id": lane["lane_id"],
                        "repository": lane["repository"],
                        "state": lane["state"],
                        "reason": lane["reason"],
                    }
                    for lane in lane_candidates
                ],
            }
        )
    event_kinds = {
        str(event.get("kind") or event.get("status") or "").upper()
        for event in (events or [])
        if isinstance(event, dict)
    }
    wake_events = {
        "MERGED",
        "PASS",
        "FAIL",
        "PREFLIGHT_GREEN",
        "CI_GREEN",
        "CI_FAILED",
        "PRODUCT_REALITY_VERIFIED",
    }
    candidate_dispatch_queue = list(dispatch_queue)
    jev_decision_harvest = _harvest_jev_decisions(
        shadow_candidates=shadow_candidates,
        candidate_dispatch_queue=candidate_dispatch_queue,
        compacted=compacted,
    )
    if machine_health is None:
        steward = {
            "status": "NOT_EVALUATED",
            "reason": "machine health snapshot not supplied",
            "admitted": candidate_dispatch_queue,
            "deferred": [],
            "next_fairness_cursor": (steward_state or {}).get("last_admitted_project"),
        }
    else:
        assessment = assess_machine_health(machine_health)
        steward = govern_dispatch(
            candidate_dispatch_queue,
            assessment,
            last_admitted_project=(steward_state or {}).get("last_admitted_project"),
        )
        dispatch_queue = steward["admitted"]
        admitted_ids = {lane["lane_id"] for lane in dispatch_queue}
        for row in ledgers:
            selected_lane = row.get("selected_lane")
            if not isinstance(selected_lane, dict):
                continue
            if selected_lane.get("lane_id") in admitted_ids:
                row["steward_admission"] = "ADMITTED"
            else:
                row["steward_admission"] = "DEFERRED_MACHINE_HEALTH"
                row["next_gate"] = "WAIT_STEWARD_MACHINE_CAPACITY"

    red_health = machine_health is not None and steward.get("assessment", {}).get(
        "band"
    ) in {"RED", "CRITICAL"}
    immediate = bool(dispatch_queue) or (
        bool(event_kinds & wake_events) and not red_health
    )

    return {
        "schema_version": 1,
        "controller": FOREMAN_NAME,
        "authority": "EPHEMERAL_PROJECTION_OF_LIVE_EVIDENCE",
        "projects": ledgers,
        "candidate_dispatch_queue": candidate_dispatch_queue,
        "dispatch_queue": dispatch_queue,
        "steward": steward,
        "dispatchable_product_projects": [
            lane["project_id"] for lane in dispatch_queue
        ],
        "waiting_lanes_rotated_away_from": sorted(set(rotated_waits)),
        "execution_compaction": compacted,
        "jev_shadow": jev_shadow,
        "jev_decision_harvest": jev_decision_harvest,
        "jev_decision_harvest_count": len(jev_decision_harvest),
        "immediate_continuation": immediate,
        "product_advancement_rate": "UNKNOWN_UNTIL_VERIFIED_MUTATION",
        "safety_boundaries": [
            "GitHub and repository-local governing documents remain ownership authority",
            "Foreman never creates a competing manual ownership database",
            "all canonical independent product lanes are represented",
            "one substantial new slice per independent product per run",
            "waiting gates are asynchronous and never consume another product lane",
            "Product Reality is never inferred",
            "Foreman selects and dispatches but never merges",
            "Steward may throttle or defer execution for machine health without classifying the product as blocked",
            "Jev shadow observes independently eligible candidates but cannot alter Foreman selection or dispatch",
        ],
    }
