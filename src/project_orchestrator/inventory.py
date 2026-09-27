from __future__ import annotations

import json
from pathlib import Path
from typing import Any


EXPECTED_REPOSITORIES = {
    "jweter/knowledge-engine-core",
    "jweter/knowledge-engine-web",
    "jweter/knowledge-engine-ai",
    "jweter/rocksmith-cdlc-generator",
    "jweter/Project-Everward",
    "jweter/VoiceStudio",
}


def load_inventory(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_inventory(payload)
    return payload


def validate_inventory(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != 1:
        raise ValueError("portfolio schema_version must be 1")
    projects = payload.get("projects")
    if not isinstance(projects, list) or not projects:
        raise ValueError("portfolio projects must be a non-empty list")

    ids: set[str] = set()
    repos: set[str] = set()
    for project in projects:
        project_id = project.get("id")
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("every project requires a non-empty id")
        if project_id in ids:
            raise ValueError(f"duplicate project id: {project_id}")
        ids.add(project_id)

        entries = project.get("repositories")
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"{project_id} requires repositories")
        for entry in entries:
            name = entry.get("name")
            if not isinstance(name, str) or "/" not in name:
                raise ValueError(f"invalid repository name in {project_id}")
            if name in repos:
                raise ValueError(f"repository appears more than once: {name}")
            repos.add(name)

    if repos != EXPECTED_REPOSITORIES:
        missing = sorted(EXPECTED_REPOSITORIES - repos)
        extra = sorted(repos - EXPECTED_REPOSITORIES)
        raise ValueError(f"portfolio repository mismatch; missing={missing}, extra={extra}")
\n