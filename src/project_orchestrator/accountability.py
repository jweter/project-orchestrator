"""Closed-loop accountability for runtime consumption and roadmap advancement."""
from __future__ import annotations

from typing import Any


def runtime_acceptance(merged: list[dict[str, Any]], runtime: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep each merged control-plane SHA open until the authoritative runtime proves consumption."""
    live_sha = runtime.get("actor_revision")
    instance = runtime.get("process_instance_id")
    heartbeat_fresh = runtime.get("heartbeat_fresh") is True
    state_fresh = runtime.get("state_fresh") is True
    dispatch_fresh = runtime.get("dispatch_fresh") is True
    productive = runtime.get("productive_flow_proven") is True
    out: list[dict[str, Any]] = []
    for change in merged:
        sha = change.get("merge_sha")
        if live_sha != sha:
            state = "MERGED_NOT_SYNCED" if runtime.get("checkout_revision") != sha else "SYNCED_NOT_LOADED"
        elif not instance or not heartbeat_fresh or not state_fresh or not dispatch_fresh:
            state = "STALE_GENERATION"
        elif not productive:
            state = "ACCEPTANCE_UNPROVEN"
        else:
            state = "ACCEPTED"
        out.append({**change, "state": state, "required_action": "close" if state == "ACCEPTED" else "repair_runtime"})
    return out


def assert_runtime_accountability(merged: list[dict[str, Any]], runtime: dict[str, Any], handled: list[dict[str, Any]]) -> None:
    expected = {(d.get("repository"), d.get("merge_sha"), d["state"]) for d in runtime_acceptance(merged, runtime) if d["state"] != "ACCEPTED"}
    observed = {(d.get("repository"), d.get("merge_sha"), d.get("state")) for d in handled}
    missing = expected - observed
    if missing:
        raise RuntimeError(f"P1 RUNTIME_ACCOUNTABILITY_DEFECT: {sorted(missing)!r}")


def roadmap_accountability(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Require unfinished roadmaps to own a concrete next development disposition."""
    obligations: list[dict[str, Any]] = []
    for project in snapshot.get("projects", []):
        for repo in project.get("repositories", []):
            if repo.get("roadmap_complete") is True:
                continue
            name = repo["name"]
            active = repo.get("started_work") or []
            candidate = repo.get("new_roadmap_slice")
            if active:
                state, action = "SLICE_ACTIVE", "finish_started_work"
            elif candidate:
                state, action = "SLICE_READY", "new_roadmap_slice"
            elif repo.get("capacity_available") is True:
                state, action = "ROADMAP_PIPELINE_IDLE", "create_next_slice"
            else:
                state, action = "WAITING_CAPACITY", "wait_capacity"
            obligations.append({"project": project["id"], "repository": name, "state": state, "required_action": action})
    return obligations


def assert_roadmap_accountability(snapshot: dict[str, Any], handled: list[dict[str, Any]]) -> None:
    expected = {(d["repository"], d["state"], d["required_action"]) for d in roadmap_accountability(snapshot) if d["state"] != "WAITING_CAPACITY"}
    observed = {(d.get("repository"), d.get("state"), d.get("required_action")) for d in handled}
    missing = expected - observed
    if missing:
        raise RuntimeError(f"P1 ROADMAP_ACCOUNTABILITY_DEFECT: {sorted(missing)!r}")
