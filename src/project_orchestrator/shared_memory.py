from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .learning import validate_memory

DEFAULT_ENV = "AZATHOTH_SHARED_MEMORY_DIR"
DEFAULT_RELATIVE_ROOT = Path(".azathoth") / "shared-memory"
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_./-]{1,}")

TEXT_FIELDS = (
    "symptom",
    "root_cause",
    "fix",
    "verification",
    "regression_protection",
    "residual_risk",
)


def default_shared_memory_root() -> Path:
    configured = os.environ.get(DEFAULT_ENV)
    if configured:
        return Path(configured).expanduser()
    return Path.home() / DEFAULT_RELATIVE_ROOT


def _tokens(value: str) -> set[str]:
    return {token for token in _TOKEN_RE.findall(value.lower()) if len(token) >= 3}


def build_hierarchical_index(memory: dict[str, Any]) -> dict[str, Any]:
    normalized = validate_memory(memory)
    tree: dict[str, dict[str, dict[str, list[str]]]] = {}
    for entry in normalized["entries"]:
        project = entry["project"]
        repository = entry["repository"]
        source_type = entry["source_type"]
        tree.setdefault(project, {}).setdefault(repository, {}).setdefault(source_type, []).append(
            entry["lesson_id"]
        )

    for repositories in tree.values():
        for source_types in repositories.values():
            for lesson_ids in source_types.values():
                lesson_ids.sort()

    return {
        "schema_version": 1,
        "kind": "azathoth-shared-engineering-memory-index",
        "entry_count": len(normalized["entries"]),
        "tree": tree,
    }


def render_text(memory: dict[str, Any]) -> str:
    normalized = validate_memory(memory)
    lines = [
        "# Azathoth Shared Engineering Memory",
        "",
        "Only VERIFIED, sanitized engineering lessons are materialized here.",
        "Repository/product authority and current live evidence always outrank memory.",
        "",
    ]
    for entry in sorted(
        normalized["entries"],
        key=lambda item: (item["project"], item["repository"], item["source_type"], item["lesson_id"]),
    ):
        lines.extend(
            [
                f"## {entry['lesson_id']} — {entry['repository']}",
                f"project: {entry['project']}",
                f"source_type: {entry['source_type']}",
                f"source_ref: {entry['source_ref']}",
                f"symptom: {entry['symptom']}",
                f"root_cause: {entry['root_cause']}",
                f"fix: {entry['fix']}",
                f"verification: {entry['verification']}",
                f"regression_protection: {entry['regression_protection']}",
                f"residual_risk: {entry['residual_risk']}",
                "evidence_state: VERIFIED",
                "",
            ]
        )
    return "\n".join(lines)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def sync_shared_memory(memory: dict[str, Any], root: str | Path | None = None) -> dict[str, Any]:
    normalized = validate_memory(memory)
    destination = Path(root) if root is not None else default_shared_memory_root()
    destination = destination.expanduser()
    index = build_hierarchical_index(normalized)

    memory_path = destination / "memory.json"
    text_path = destination / "memory.txt"
    index_path = destination / "index.json"

    _atomic_write(memory_path, json.dumps(normalized, indent=2, sort_keys=True) + "\n")
    _atomic_write(index_path, json.dumps(index, indent=2, sort_keys=True) + "\n")
    _atomic_write(text_path, render_text(normalized))

    return {
        "state": "SYNCED",
        "root": str(destination),
        "entry_count": len(normalized["entries"]),
        "files": {
            "memory": str(memory_path),
            "index": str(index_path),
            "text": str(text_path),
        },
    }


def load_shared_memory(root: str | Path | None = None) -> dict[str, Any]:
    destination = Path(root) if root is not None else default_shared_memory_root()
    payload = json.loads((destination.expanduser() / "memory.json").read_text(encoding="utf-8"))
    return validate_memory(payload)


def query_memory(
    memory: dict[str, Any],
    *,
    project: str | None = None,
    repository: str | None = None,
    terms: list[str] | None = None,
    source_type: str | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    if limit < 1:
        raise ValueError("limit must be at least 1")
    normalized = validate_memory(memory)
    query_tokens = _tokens(" ".join(terms or []))

    matches: list[tuple[int, dict[str, Any], list[str]]] = []
    for entry in normalized["entries"]:
        reasons: list[str] = []
        score = 0

        if project is not None:
            if entry["project"] != project:
                continue
            score += 40
            reasons.append("project")

        if repository is not None:
            if entry["repository"] != repository:
                continue
            score += 60
            reasons.append("repository")

        if source_type is not None:
            if entry["source_type"] != source_type:
                continue
            score += 20
            reasons.append("source_type")

        haystack = " ".join(str(entry[field]) for field in TEXT_FIELDS)
        overlap = sorted(query_tokens & _tokens(haystack))
        if query_tokens and not overlap:
            continue
        if overlap:
            score += len(overlap) * 10
            reasons.extend(f"term:{token}" for token in overlap)

        matches.append((score, entry, reasons))

    matches.sort(key=lambda item: (-item[0], item[1]["lesson_id"]))
    selected = matches[:limit]
    return {
        "state": "MATCHES" if selected else "NO_MATCH",
        "query": {
            "project": project,
            "repository": repository,
            "source_type": source_type,
            "terms": list(terms or []),
            "limit": limit,
        },
        "results": [
            {
                "score": score,
                "match_reasons": reasons,
                "lesson": entry,
            }
            for score, entry, reasons in selected
        ],
    }
