from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from scripts.project_capability_graph import validate
from .state_io import atomic_json


class CapabilityAuthorityError(RuntimeError):
    pass


def compile_capability_authority(
    state_root: Path,
    repository: str,
    exact_head: str,
    control_plane: Mapping[str, Any],
    *,
    read_authoritative_file,
) -> dict[str, Any]:
    """Compile an explicitly declared repository capability graph into runtime authority.

    Human roadmap prose is never converted into mutation authority here. The repository
    must explicitly name a machine-readable JSON graph in capability_graph, and that
    path must also be listed in authoritative_documents.
    """
    graph_path = control_plane.get("capability_graph")
    docs = control_plane.get("authoritative_documents")
    if not isinstance(graph_path, str) or not graph_path.strip():
        raise CapabilityAuthorityError("CAPABILITY_GRAPH_DECLARATION_REQUIRED")
    if not isinstance(docs, list) or graph_path not in docs:
        raise CapabilityAuthorityError("CAPABILITY_GRAPH_NOT_AUTHORITATIVE")

    raw = read_authoritative_file(repository, exact_head, graph_path)
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise CapabilityAuthorityError("CAPABILITY_GRAPH_MALFORMED") from exc
    if not isinstance(payload, dict):
        raise CapabilityAuthorityError("CAPABILITY_GRAPH_MALFORMED")
    if payload.get("repository") not in {None, repository}:
        raise CapabilityAuthorityError("CAPABILITY_GRAPH_REPOSITORY_MISMATCH")

    report = validate(payload)
    if report["status"] == "DIRECTION_REQUIRED":
        raise CapabilityAuthorityError("CAPABILITY_GRAPH_INVALID")

    compiled = dict(payload)
    compiled["repository"] = repository
    compiled["authority"] = {
        "source": graph_path,
        "exact_head": exact_head,
        "control_plane_schema_version": control_plane.get("schema_version"),
    }
    target = state_root / "capability-graphs" / f"{repository.replace('/', '__')}.json"
    atomic_json(target, compiled)
    return {"status": "COMPILED", "path": str(target), "validation": report, "graph": compiled}
