from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from .state_io import atomic_json


def replenish_frontiers(
    workspace_root: Path,
    state_root: Path,
    *,
    compile_fn: Callable,
    load_slice_fn: Callable,
) -> dict:
    """Consume frontier refresh requests using only current declared authority.

    This function never authors product requirements. It recompiles repository
    declarations and proves whether a bounded next slice now exists.
    """
    continuation_path = state_root / "continuation-request.json"
    requests: dict[str, dict] = {}
    try:
        continuation = json.loads(continuation_path.read_text(encoding="utf-8"))
        repo = continuation.get("repository")
        if isinstance(repo, str) and repo:
            requests[repo] = {"source": "CONTINUATION", **continuation}
    except (OSError, json.JSONDecodeError, TypeError):
        pass

    direction_root = state_root / "direction-requests"
    if direction_root.is_dir():
        for path in sorted(direction_root.glob("*.json")):
            try:
                request = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
            repo = request.get("repository")
            if request.get("status") == "DIRECTION_REQUIRED" and isinstance(repo, str) and repo:
                requests.setdefault(repo, {"source": "DIRECTION", **request})

    if not requests:
        result = {"schema_version": 1, "status": "NO_REQUESTS", "repositories": {}}
        atomic_json(state_root / "frontier-replenishment.json", result)
        return result

    compilation = compile_fn(workspace_root, state_root)
    compiled = compilation.get("repositories", {}) if isinstance(compilation, dict) else {}
    results: dict[str, dict] = {}
    for repo, request in requests.items():
        row = compiled.get(repo, {}) if isinstance(compiled, dict) else {}
        exact_head = row.get("exact_head") or request.get("exact_head")
        if row.get("status") != "COMPILED" or not exact_head:
            results[repo] = {
                "status": "DIRECTION_REQUIRED",
                "reason": row.get("reason") or "CURRENT_AUTHORITY_NOT_COMPILED",
                "source": request.get("source"),
            }
            continue
        try:
            execution = load_slice_fn(state_root, repo, exact_head)
        except RuntimeError as exc:
            results[repo] = {
                "status": "DIRECTION_REQUIRED",
                "reason": str(exc),
                "source": request.get("source"),
                "exact_head": exact_head,
            }
            continue
        if execution.get("status") == "NO_WORK":
            results[repo] = {
                "status": "DIRECTION_REQUIRED",
                "reason": execution.get("reason") or "NO_WORK",
                "source": request.get("source"),
                "exact_head": exact_head,
            }
            continue

        direction_path = direction_root / f"{repo.replace('/', '__')}.json"
        direction_path.unlink(missing_ok=True)
        try:
            current = json.loads(continuation_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            current = {}
        if current.get("repository") == repo:
            continuation_path.unlink(missing_ok=True)
        results[repo] = {
            "status": "REPLENISHED",
            "source": request.get("source"),
            "exact_head": exact_head,
            "slice_id": execution.get("slice_id"),
            "capability_id": execution.get("capability_id"),
        }

    status = "REPLENISHED" if any(r["status"] == "REPLENISHED" for r in results.values()) else "DIRECTION_REQUIRED"
    payload = {
        "schema_version": 1,
        "status": status,
        "repositories": results,
        "recorded_at_utc": datetime.now(UTC).isoformat(),
    }
    atomic_json(state_root / "frontier-replenishment.json", payload)
    return payload
