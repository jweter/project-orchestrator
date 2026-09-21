"""Deterministic blocker companion for the autonomous worker.

The Pit Mate never mutates product repositories and never grants authority. It
turns machine state into a bounded next-action diagnosis for the manager.
"""
from __future__ import annotations

from typing import Any


def diagnose(builder: dict[str, Any], liveness: dict[str, Any]) -> dict[str, str]:
    status = str(builder.get("status", "UNKNOWN"))
    reason = str(builder.get("reason", ""))

    if status == "USER_ACTIVE":
        return {
            "status": "WAITING_POLITELY",
            "action": "Retry when the Windows idle gate is satisfied; do not compete with the user.",
            "voice": "Easy, mate. I'll mind the lamp while thou's busy above. We'll get back to the seam when the floor's clear.",
        }
    if "CAPABILITY_GRAPH_REQUIRED" in reason or status == "CAPABILITY_GRAPH_REQUIRED":
        return {
            "status": "DIRECTION_REQUIRED",
            "action": "Find explicit repository/GitHub authority and compile it into a capability graph; never invent product direction.",
            "voice": "We've coal in the hill but no marked seam, mate. I'll fetch the plans; we don't swing blind.",
        }
    if status == "NO_READY_EXECUTION_SLICE":
        return {
            "status": "SLICE_REQUIRED",
            "action": "Inspect the direction request and capability graph for a missing or blocked executable slice.",
            "voice": "There's work in the pit, but no safe face marked for the pick. I'll check the workings with thee.",
        }
    if status in {"BUILDER_FAILED", "P1_BUILDER_FAILURE", "PROCESS_FAILURE", "PREFLIGHT_FAILED", "PUSH_FAILED", "PR_CREATE_FAILED", "STALE_BUILDER_STATE", "STATE_UNREADABLE"}:
        return {
            "status": "REPAIR_REQUIRED",
            "action": "Classify sanitized failure evidence, attempt only an authorized bounded repair, then independently verify.",
            "voice": "Rough shift, mate. Hand us the safe failure marks and we'll find where the timber gave.",
        }
    if liveness.get("status") == "ELIGIBLE_IDLE":
        return {
            "status": "LIVENESS_DEFECT",
            "action": "Reacquire eligible work; an authorized idle lane is an orchestration defect.",
            "voice": "No sense leaning on the shovel with a seam ready. I'll give the foreman a knock.",
        }
    return {
        "status": "STANDING_BY",
        "action": "No bounded intervention required.",
        "voice": "Aye, mate. Lamp's lit and I'm alongside.",
    }
