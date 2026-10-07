from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .capability_applicability import (
    build_applicability_index,
    match_applicable_capabilities,
)

PROJECTS = {"knowledge-engine", "rocksmith", "everward", "voicestudio"}
SOURCE_TYPES = {"issue", "pull_request", "decomposition", "verified_learning", "product_reality_debt"}
TERMINAL_SOURCE_STATES = {"CLOSED", "MERGED", "REMOVED", "CANCELLED"}
REQUIRED = {"project", "repository", "source_type", "source_ref", "source_revision", "source_state", "priority"}


class CandidateRegistryError(ValueError):
    pass


def _candidate_id(item: dict[str, Any]) -> str:
    stable = {key: item[key] for key in ("project", "repository", "source_type", "source_ref")}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:20]


def validate_candidate(item: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise CandidateRegistryError("candidate must be an object")
    missing = sorted(REQUIRED - item.keys())
    if missing:
        raise CandidateRegistryError(f"candidate missing required fields: {missing}")
    if item["project"] not in PROJECTS:
        raise CandidateRegistryError("candidate project is not an independent product")
    if item["source_type"] not in SOURCE_TYPES:
        raise CandidateRegistryError("unsupported candidate source_type")
    for field in ("repository", "source_ref", "source_revision", "source_state"):
        if not isinstance(item[field], str) or not item[field].strip():
            raise CandidateRegistryError(f"{field} must be a non-empty string")
    if item["priority"] not in {"P0", "P1", "P2", "P3", "P4", "P5", "P6"}:
        raise CandidateRegistryError("priority must be P0..P6")
    paths = item.get("paths", [])
    if not isinstance(paths, list) or not all(isinstance(path, str) and path for path in paths):
        raise CandidateRegistryError("paths must be a string list")
    dependencies = item.get("dependencies", [])
    if not isinstance(dependencies, list) or not all(isinstance(dep, str) and dep for dep in dependencies):
        raise CandidateRegistryError("dependencies must be a string list")
    normalized = dict(item)
    normalized["paths"] = sorted(set(paths))
    normalized["dependencies"] = sorted(set(dependencies))
    normalized["candidate_id"] = _candidate_id(normalized)
    if item.get("candidate_id") not in {None, normalized["candidate_id"]}:
        raise CandidateRegistryError("candidate_id does not match authoritative source identity")
    return normalized


def validate_registry(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise CandidateRegistryError("registry schema_version must equal 1")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise CandidateRegistryError("registry entries must be a list")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in entries:
        item = validate_candidate(raw)
        if item["candidate_id"] in seen:
            raise CandidateRegistryError(f"duplicate candidate_id: {item['candidate_id']}")
        seen.add(item["candidate_id"])
        normalized.append(item)
    normalized.sort(key=lambda item: item["candidate_id"])
    return {"schema_version": 1, "entries": normalized}


def refresh_registry(registry: dict[str, Any], authoritative: list[dict[str, Any]]) -> dict[str, Any]:
    """Idempotently refresh source state while retaining vanished entries as REMOVED evidence."""
    current = {item["candidate_id"]: item for item in validate_registry(registry)["entries"]}
    incoming: dict[str, dict[str, Any]] = {}
    for raw in authoritative:
        item = validate_candidate(raw)
        if item["candidate_id"] in incoming:
            raise CandidateRegistryError(f"duplicate authoritative source: {item['source_ref']}")
        incoming[item["candidate_id"]] = item

    merged: list[dict[str, Any]] = []
    for cid in sorted(set(current) | set(incoming)):
        if cid in incoming:
            old = current.get(cid)
            new = incoming[cid]
            if old and old["source_revision"] != new["source_revision"]:
                new = {**new, "previous_source_revision": old["source_revision"], "head_sensitive_evidence_valid": False}
            merged.append(new)
        else:
            old = current[cid]
            merged.append({**old, "source_state": "REMOVED", "head_sensitive_evidence_valid": False})
    return validate_registry({"schema_version": 1, "entries": merged})


def attach_verified_learning(
    candidate: dict[str, Any],
    memory: dict[str, Any],
    skill_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach only explicitly applicable verified capability references.

    Free-form learning similarity is not authority for packet reuse. When a
    validated repository-skill catalog is unavailable, fail closed and attach
    no capability.
    """
    item = validate_candidate(candidate)
    if skill_catalog is None:
        return {
            **item,
            "capabilities_to_reuse": [],
            "capability_reuse_refs": [],
            "learning_gate": "CAPABILITY_INDEX_REQUIRED",
            "product_reality_inferred": False,
        }

    index = build_applicability_index(skill_catalog, memory)
    result = match_applicable_capabilities(
        index,
        project=item["project"],
        repository=item["repository"],
        changed_paths=item["paths"],
        task_summary=str(item.get("task") or item.get("title") or ""),
    )
    matches = result["applicable_capabilities"]
    return {
        **item,
        "capabilities_to_reuse": [match["capability_id"] for match in matches],
        "capability_reuse_refs": matches,
        "learning_gate": result["selection_state"],
        "product_reality_inferred": False,
    }


def registry_snapshot(registry: dict[str, Any], *, capacity_evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    candidates = []
    entries = validate_registry(registry)["entries"]
    ids = {item["candidate_id"] for item in entries}
    terminal = {item["candidate_id"] for item in entries if item["source_state"].upper() in TERMINAL_SOURCE_STATES}
    for item in entries:
        deps = item["dependencies"]
        dependencies_clear = all(dep in ids and dep in terminal for dep in deps)
        terminal_source = item["source_state"].upper() in TERMINAL_SOURCE_STATES
        dispatchable_scope = bool(item["paths"]) and bool(item.get("task"))
        candidates.append({
            **item,
            "id": item["candidate_id"],
            "state": item["priority"],
            "dependencies_clear": dependencies_clear,
            "conflict_free": item.get("conflict_free") is True,
            "actively_owned": item.get("actively_owned") is True,
            "product_reality_required": item.get("product_reality_required") is True,
            "terminal_source": terminal_source,
            "dispatchable_scope": dispatchable_scope,
            "registry_ready": not terminal_source and dispatchable_scope,
        })
    return {"schema_version": 1, "candidates": candidates, "capacity_evidence": capacity_evidence or {}}


def select_release_frontier(registry: dict[str, Any]) -> list[dict[str, Any]]:
    """Prioritize verified release blockers without overriding P0-P4 or ownership.

    Only an explicit, source-revision-bound gate reference qualifies as release
    evidence. Unknown impact never becomes a guessed score or a reason to idle.
    """
    snapshot = registry_snapshot(registry)
    ready = [
        item for item in snapshot["candidates"]
        if item["registry_ready"] and item["dependencies_clear"]
        and item["conflict_free"] and not item["actively_owned"]
    ]

    def release_evidence(item: dict[str, Any]) -> bool:
        gate = item.get("usability_gate_ref")
        return (
            isinstance(gate, str) and bool(gate.strip())
            and item.get("usability_gate_source_revision") == item["source_revision"]
            and item.get("usability_gate_verified") is True
        )

    return sorted(
        ready,
        key=lambda item: (
            int(item["priority"][1:]),
            not release_evidence(item),
            item["candidate_id"],
        ),
    )


def load_registry(path: str | Path) -> dict[str, Any]:
    return validate_registry(json.loads(Path(path).read_text(encoding="utf-8")))


def write_registry(path: str | Path, registry: dict[str, Any]) -> None:
    normalized = validate_registry(registry)
    Path(path).write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n", encoding="utf-8")
