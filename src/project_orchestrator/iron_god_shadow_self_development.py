"""Read-only Iron God self-development candidate projection.

Uses the existing development-team conflict detector. This never dispatches,
reserves a durable lease, changes owner controls, or approves its own release.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Any, Mapping

from .team_runtime import build_ephemeral_leases

REPOSITORY = "jweter/project-orchestrator"
PROJECT_ID = "iron-god"
_HEX_HEAD = re.compile(r"[a-f0-9]{40}", re.ASCII)


def _bounded_paths(value: object) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > 20:
        raise ValueError("SELF_DEVELOPMENT_PATHS_INVALID")
    paths: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or "\\" in item:
            raise ValueError("SELF_DEVELOPMENT_PATHS_INVALID")
        path = PurePosixPath(item)
        if (
            path.is_absolute()
            or item.startswith("./")
            or "." in path.parts
            or ".." in path.parts
            or any(part in {"", ".git"} for part in path.parts)
            or not (
                item.startswith("src/project_orchestrator/")
                or item.startswith("tests/")
            )
        ):
            raise ValueError("SELF_DEVELOPMENT_PATHS_OUTSIDE_SAFE_SCOPE")
        paths.append(str(path))
    if not any(p.startswith("src/project_orchestrator/") for p in paths):
        raise ValueError("SELF_DEVELOPMENT_SOURCE_REQUIRED")
    if not any(p.startswith("tests/") for p in paths):
        raise ValueError("SELF_DEVELOPMENT_TESTS_REQUIRED")
    return sorted(set(paths))


def plan_shadow_self_development(
    candidate: Mapping[str, Any],
    *,
    live_active_work: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Project one self-repair candidate without creating dispatch eligibility.

    Candidate metadata is caller-supplied and UNVERIFIED. Only the real
    controller, lease store, owner control, and independent runner may ever
    authorize implementation. A successful shadow projection is not such proof.
    """
    if (
        not isinstance(candidate, Mapping)
        or candidate.get("project_id") != PROJECT_ID
        or candidate.get("repository") != REPOSITORY
    ):
        raise ValueError("SELF_DEVELOPMENT_REPOSITORY_UNVERIFIED")
    head = candidate.get("base_sha")
    branch = candidate.get("branch")
    if not isinstance(head, str) or _HEX_HEAD.fullmatch(head) is None:
        raise ValueError("SELF_DEVELOPMENT_BASE_UNVERIFIED")
    if (
        not isinstance(branch, str)
        or len(branch) > 128
        or not (branch.startswith("iron-god/") or branch.startswith("fix/iron-god-"))
        or ".." in branch
        or branch.endswith("/")
        or "\\" in branch
    ):
        raise ValueError("SELF_DEVELOPMENT_BRANCH_UNVERIFIED")
    title = candidate.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise ValueError("SELF_DEVELOPMENT_OBJECTIVE_UNVERIFIED")
    paths = _bounded_paths(candidate.get("paths"))
    contract = {
        "contract_id": "shadow-"
        + hashlib.sha256(
            json.dumps(
                {
                    "repository": REPOSITORY,
                    "branch": branch,
                    "head": head,
                    "paths": paths,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:20],
        "project_id": PROJECT_ID,
        "repository": REPOSITORY,
        "branch": branch,
        "base_sha": head,
        "paths": paths,
    }
    # The standard lease planner returns an EPHEMERAL CONFLICT PROJECTION,
    # never a real SQLite or Git lease. No worker is submitted.
    projection = build_ephemeral_leases([contract], live_active_work or [])[0]
    conflict = projection["state"] == "CONFLICT"
    return {
        "schema_version": 1,
        "status": "SHADOW_PATH_CONFLICT"
        if conflict
        else "SHADOW_SELF_REPAIR_CANDIDATE",
        "project_id": PROJECT_ID,
        "repository": REPOSITORY,
        "candidate": {
            "title": title.strip(),
            "base_sha": head,
            "branch": branch,
            "paths": paths,
            "metadata_provenance": "CALLER_SUPPLIED_UNVERIFIED",
        },
        "conflict": projection["reason"] if conflict else None,
        "execution_authorized": False,
        "dispatch_created": False,
        "lease_acquired": False,
        "controller_mode_changed": False,
        "merge_authorized": False,
        "next_gate": "INDEPENDENT_OWNER_CONTROL_RUNNER_AND_EXACT_GIT_AUTHORITY",
    }
