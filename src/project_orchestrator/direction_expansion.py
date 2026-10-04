from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from scripts.project_capability_graph import validate
from .state_io import atomic_json


class DirectionExpansionError(RuntimeError):
    pass


def compile_direction_expansion(
    state_root: Path,
    repository: str,
    exact_head: str,
    control_plane: Mapping[str, Any],
    usability_gate: Mapping[str, Any],
    *,
    read_authoritative_file,
) -> dict:
    """Compile repository-declared machine direction into a proposed capability graph.

    Direction expansion is fail-closed: prose roadmaps and the portfolio usability
    gate provide intent/context but never mutation authority by themselves.
    """
    source = control_plane.get("direction_graph")
    docs = control_plane.get("authoritative_documents")
    if not isinstance(source, str) or not source.strip():
        raise DirectionExpansionError("DIRECTION_GRAPH_DECLARATION_REQUIRED")
    if not isinstance(docs, list) or source not in docs:
        raise DirectionExpansionError("DIRECTION_GRAPH_NOT_AUTHORITATIVE")
    raw = read_authoritative_file(repository, exact_head, source)
    try:
        direction = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise DirectionExpansionError("DIRECTION_GRAPH_MALFORMED") from exc
    if not isinstance(direction, dict):
        raise DirectionExpansionError("DIRECTION_GRAPH_MALFORMED")
    if direction.get("repository") not in {None, repository}:
        raise DirectionExpansionError("DIRECTION_GRAPH_REPOSITORY_MISMATCH")
    candidates = direction.get("next_capabilities")
    if not isinstance(candidates, list) or not candidates:
        raise DirectionExpansionError("NEXT_CAPABILITIES_REQUIRED")

    gate = usability_gate.get("next_gate")
    gate_text = usability_gate.get("gate")
    if not isinstance(gate, str) or not gate or not isinstance(gate_text, str) or not gate_text:
        raise DirectionExpansionError("USABILITY_GATE_REQUIRED")

    capabilities = []
    roadmap = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise DirectionExpansionError("DIRECTION_CAPABILITY_MALFORMED")
        if candidate.get("target_gate") != gate:
            continue
        capability = {
            key: candidate[key]
            for key in ("id", "capability", "state", "depends_on", "completion_evidence", "slices")
            if key in candidate
        }
        capabilities.append(capability)
        roadmap.append(candidate.get("id"))
    if not capabilities:
        raise DirectionExpansionError("NO_CAPABILITY_FOR_CURRENT_USABILITY_GATE")

    proposal = {
        "schema_version": 1,
        "repository": repository,
        "capabilities": capabilities,
        "roadmap": roadmap,
    }
    report = validate(proposal)
    if report["status"] == "DIRECTION_REQUIRED":
        raise DirectionExpansionError("EXPANDED_CAPABILITY_GRAPH_INVALID")

    payload = {
        "schema_version": 1,
        "status": "PROPOSED",
        "repository": repository,
        "exact_head": exact_head,
        "target_gate": gate,
        "gate": gate_text,
        "authority": {
            "direction_source": source,
            "direction_exact_head": exact_head,
            "usability_source": "engineering/product-usability-gates.json",
        },
        "graph": proposal,
        "validation": report,
        "recorded_at_utc": datetime.now(UTC).isoformat(),
    }
    atomic_json(state_root / "direction-expansions" / f"{repository.replace('/', '__')}.json", payload)
    return payload
