"""Read-only Iron God self-development source provenance probe.

This is diagnostic evidence, not execution authority. It never creates refs,
claims, worktrees, workers, or a replacement controller. The resident dispatch
and owner-control gates remain independently binding.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Mapping

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_ENGINE = "jweter/project-orchestrator"


def _git(repo: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def inspect_self_development_source(
    repo: Path,
    activation: Mapping[str, object],
    controller: Mapping[str, object],
) -> dict[str, object]:
    """Report source and controller evidence without granting execution."""
    reasons: list[str] = []
    products = activation.get("products")
    iron_god = products.get("iron-god") if isinstance(products, dict) else None
    enabled = isinstance(iron_god, dict) and iron_god.get("enabled") is True
    repositories = iron_god.get("repositories") if enabled else None
    selected = repositories.get(_ENGINE) if isinstance(repositories, dict) else None
    if not enabled:
        reasons.append("IRON_GOD_ACTIVATION_MISSING")
    if not isinstance(selected, str) or not _SHA.fullmatch(selected):
        reasons.append("EXACT_ACTIVATION_HEAD_MISSING")
        selected = None

    accepted = _git(repo, "show-ref", "--verify", "--hash", "refs/iron-god/accepted")
    if accepted is None or not _SHA.fullmatch(accepted):
        reasons.append("PROTECTED_ACCEPTED_BASELINE_MISSING")
        accepted = None
    if selected is not None:
        if _git(repo, "cat-file", "-t", selected) != "commit":
            reasons.append("ACTIVATION_COMMIT_NOT_LOCAL")
        if accepted is not None and selected != accepted:
            reasons.append("ACTIVATION_NOT_ACCEPTED_LOCAL_HEAD")
    if (
        controller.get("mode") != "candidate_mode"
        or controller.get("candidate_execution_authorized") is not True
    ):
        reasons.append("CANDIDATE_EXECUTION_NOT_AUTHORIZED")
    return {
        "schema_version": 1,
        "repository": _ENGINE,
        "selected_sha": selected,
        "accepted_sha": accepted,
        "status": "BLOCKED" if reasons else "SOURCE_PROVENANCE_VERIFIED_ONLY",
        "reasons": reasons,
        "dispatch_authorized": False,
    }
