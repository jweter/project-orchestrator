from __future__ import annotations

from typing import Any

from .candidate_registry import (
    attach_verified_learning,
    registry_snapshot,
    validate_registry,
)
from .exponential_engine import process_event

SCHEMA_VERSION = 1


def process_registry_event(
    registry: dict[str, Any],
    *,
    memory: dict[str, Any],
    skill_catalog: dict[str, Any],
    policy: dict[str, Any],
    event: dict[str, Any],
    capacity_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bridge a persistent authoritative registry into the event dispatcher.

    Capability reuse is attached before planning, while normal registry,
    dependency, ownership, Product Reality, and capacity gates stay
    authoritative.
    """
    normalized = validate_registry(registry)
    enriched_entries = [
        attach_verified_learning(item, memory, skill_catalog)
        for item in normalized["entries"]
    ]
    snapshot = registry_snapshot(
        {"schema_version": SCHEMA_VERSION, "entries": enriched_entries},
        capacity_evidence=capacity_evidence,
    )
    outcome = process_event(snapshot, policy, event)
    plan = outcome.get("plan") or {}
    selected = plan.get("selected_work_packets", [])
    attached_count = sum(
        len(item.get("capabilities_to_reuse", []))
        for item in snapshot["candidates"]
    )
    selected_reuse_count = sum(
        len(item.get("capabilities_to_reuse", []))
        for item in selected
    )
    return {
        **outcome,
        "registry_bridge": {
            "schema_version": SCHEMA_VERSION,
            "registry_entry_count": len(snapshot["candidates"]),
            "selected_packet_count": len(selected),
            "attached_capability_count": attached_count,
            "selected_capability_reuse_count": selected_reuse_count,
            "product_reality_inferred": False,
        },
    }
