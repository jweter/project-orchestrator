"""Read-only local Git identity audit for activated portfolio repositories.

This is diagnostic evidence, not acceptance, owner-control, or dispatch authority.
No fetch, ref creation, checkout, controller mutation, or worker launch is permitted.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Mapping

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_GITHUB_REMOTE = re.compile(
    r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?\Z",
    re.IGNORECASE,
)


def _remote_repository(url: str | None) -> str | None:
    match = _GITHUB_REMOTE.fullmatch(url) if url else None
    return match.group(1).casefold() if match else None


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def audit_local_activation(
    repositories: list[str],
    activation: Mapping[str, object],
    workspace_root: Path,
) -> dict[str, object]:
    """Audit exact activation objects in canonical local workspaces, without writes."""
    products = activation.get("products")
    products = products if isinstance(products, dict) else {}
    top_heads = activation.get("heads")
    top_heads = top_heads if isinstance(top_heads, dict) else {}
    rows: dict[str, dict[str, object]] = {}

    for repository in sorted(set(repositories)):
        reasons: list[str] = []
        selected = None
        local_head = None
        accepted_head = None
        tracked_dirty = None
        if not _REPOSITORY.fullmatch(repository) or ".." in repository.split("/"):
            rows[repository] = {
                "status": "BLOCKED", "reasons": ["INVALID_REPOSITORY_ID"],
                "selected_sha": None, "local_head": None,
                "accepted_sha": None, "tracked_dirty": None,
                "dispatch_authorized": False,
            }
            continue

        for product in products.values():
            if not isinstance(product, dict) or product.get("enabled") is not True:
                continue
            mappings = product.get("repositories")
            if isinstance(mappings, dict) and repository in mappings:
                if selected is not None and selected != mappings[repository]:
                    reasons.append("CONFLICTING_PRODUCT_ACTIVATIONS")
                selected = mappings[repository]
        if not isinstance(selected, str) or not _SHA.fullmatch(selected):
            reasons.append("EXACT_ACTIVATION_HEAD_MISSING")
            selected = None
        if repository in top_heads and top_heads[repository] != selected:
            reasons.append("ACTIVATION_INTERNAL_HEAD_MISMATCH")

        root = workspace_root / repository.split("/", 1)[1]
        if not root.is_dir():
            reasons.append("LOCAL_WORKSPACE_MISSING")
        elif (actual_root := _git(root, "rev-parse", "--show-toplevel")) is None or (
            Path(actual_root).resolve() != root.resolve()
        ):
            reasons.append("LOCAL_REPOSITORY_ROOT_MISMATCH")
        else:
            remote = _git(root, "remote", "get-url", "origin")
            remote_repo = _remote_repository(remote)
            if remote_repo is None:
                reasons.append("LOCAL_REMOTE_REPOSITORY_UNVERIFIED")
            elif remote_repo != repository.casefold():
                reasons.append("LOCAL_REMOTE_REPOSITORY_MISMATCH")
            local_head = _git(root, "rev-parse", "HEAD")
            tracked = _git(root, "status", "--porcelain", "--untracked-files=no")
            tracked_dirty = None if tracked is None else bool(tracked)
            if selected is not None and _git(root, "cat-file", "-t", selected) != "commit":
                reasons.append("ACTIVATION_COMMIT_NOT_LOCAL")
            accepted_head = _git(
                root, "show-ref", "--verify", "--hash", "refs/iron-god/accepted"
            )
            if accepted_head is None:
                reasons.append("ACCEPTED_REF_MISSING")
            elif _git(root, "cat-file", "-t", accepted_head) != "commit":
                reasons.append("ACCEPTED_REF_NOT_COMMIT")
                accepted_head = None
            elif selected is not None and selected != accepted_head:
                reasons.append("ACTIVATION_NOT_ACCEPTED_LOCAL_HEAD")

        rows[repository] = {
            "status": "BLOCKED" if reasons else "LOCAL_IDENTITY_VERIFIED_ONLY",
            "reasons": sorted(set(reasons)),
            "selected_sha": selected,
            "local_head": local_head,
            "accepted_sha": accepted_head,
            "tracked_dirty": tracked_dirty,
            "dispatch_authorized": False,
        }
    return {"schema_version": 1, "repositories": rows, "dispatch_authorized": False}
