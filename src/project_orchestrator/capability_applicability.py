from __future__ import annotations

import fnmatch
from typing import Any

from .learning import validate_memory
from .skill_extraction import validate_catalog

SCHEMA_VERSION = 1


class CapabilityApplicabilityError(ValueError):
    """Raised when capability applicability evidence is malformed."""


def build_applicability_index(
    catalog: dict[str, Any], memory: dict[str, Any]
) -> dict[str, Any]:
    """Project verified repository skills into a compact applicability index."""
    normalized_memory = validate_memory(memory)
    normalized_catalog = validate_catalog(catalog, normalized_memory)
    entries: list[dict[str, Any]] = []
    for skill in normalized_catalog["skills"]:
        entries.append(
            {
                "capability_id": skill["skill_id"],
                "source_kind": "repository_skill",
                "source_ref": skill["skill_key"],
                "project": skill["project"],
                "repository": skill["repository"],
                "source_lesson_ids": list(skill["source_lesson_ids"]),
                "path_patterns": list(skill["path_patterns"]),
                "task_triggers": list(skill["task_triggers"]),
                "authority": "ADVISORY_ONLY",
                "evidence_state": "VERIFIED",
                "product_reality_may_be_inferred": False,
            }
        )
    entries.sort(key=lambda item: (item["repository"], item["source_ref"]))
    return {"schema_version": SCHEMA_VERSION, "entries": entries}


def _nonempty_strings(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise CapabilityApplicabilityError(
            f"{field} must be a list of non-empty strings"
        )
    return value


def match_applicable_capabilities(
    index: dict[str, Any],
    *,
    project: str,
    repository: str,
    changed_paths: list[str],
    task_summary: str = "",
) -> dict[str, Any]:
    """Return only explicitly matched verified capability references."""
    if not isinstance(index, dict) or index.get("schema_version") != SCHEMA_VERSION:
        raise CapabilityApplicabilityError("capability index schema_version must be 1")
    entries = index.get("entries")
    if not isinstance(entries, list):
        raise CapabilityApplicabilityError("capability index entries must be a list")
    if not isinstance(project, str) or not project.strip():
        raise CapabilityApplicabilityError("project must be a non-empty string")
    if not isinstance(repository, str) or not repository.strip():
        raise CapabilityApplicabilityError("repository must be a non-empty string")
    _nonempty_strings(changed_paths, field="changed_paths")
    if not isinstance(task_summary, str):
        raise CapabilityApplicabilityError("task_summary must be a string")

    normalized_paths = [path.replace("\\", "/") for path in changed_paths]
    lowered_summary = task_summary.lower()
    matches: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise CapabilityApplicabilityError("capability index entry must be an object")
        if entry.get("evidence_state") != "VERIFIED":
            continue
        if entry.get("authority") != "ADVISORY_ONLY":
            continue
        if entry.get("product_reality_may_be_inferred") is not False:
            continue
        if entry.get("project") != project or entry.get("repository") != repository:
            continue

        path_patterns = _nonempty_strings(
            entry.get("path_patterns"), field="path_patterns"
        )
        task_triggers = _nonempty_strings(
            entry.get("task_triggers"), field="task_triggers"
        )
        matched_paths = sorted(
            {
                path
                for path, normalized in zip(changed_paths, normalized_paths, strict=True)
                if any(fnmatch.fnmatch(normalized, pattern) for pattern in path_patterns)
            }
        )
        matched_triggers = sorted(
            trigger for trigger in task_triggers if trigger.lower() in lowered_summary
        )
        if not matched_paths and not matched_triggers:
            continue

        source_lesson_ids = _nonempty_strings(
            entry.get("source_lesson_ids"), field="source_lesson_ids"
        )
        capability_id = entry.get("capability_id")
        source_ref = entry.get("source_ref")
        if not isinstance(capability_id, str) or not capability_id:
            raise CapabilityApplicabilityError("capability_id must be a non-empty string")
        if not isinstance(source_ref, str) or not source_ref:
            raise CapabilityApplicabilityError("source_ref must be a non-empty string")
        matches.append(
            {
                "capability_id": capability_id,
                "source_kind": entry.get("source_kind"),
                "source_ref": source_ref,
                "source_lesson_ids": sorted(set(source_lesson_ids)),
                "matched_paths": matched_paths,
                "matched_task_triggers": matched_triggers,
                "authority": "ADVISORY_ONLY",
                "evidence_state": "VERIFIED",
                "reuse_outcome": "PENDING_VERIFIED_OUTCOME",
                "product_reality_may_be_inferred": False,
            }
        )

    matches.sort(key=lambda item: (item["source_ref"], item["capability_id"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "project": project,
        "repository": repository,
        "changed_paths": sorted(set(changed_paths)),
        "task_summary": task_summary,
        "applicable_capabilities": matches,
        "selection_state": (
            "VERIFIED_CAPABILITIES_SELECTED"
            if matches
            else "NO_EXPLICIT_CAPABILITY_MATCH"
        ),
        "authority_note": (
            "capability references are advisory verified-history context only; "
            "repository authority and Product Reality gates remain controlling"
        ),
    }
