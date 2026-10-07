from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from .state_io import atomic_json
from .issue_closure import build_issue_work_inventory, merge_issue_dispositions


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


def reconcile_issue_frontier(
    portfolio_snapshot: dict,
    state_root: Path,
) -> dict:
    """Reconcile complete live issue evidence into the executable product frontier.

    Live GitHub evidence controls issue existence. Durable dispositions enrich
    only still-live issues and cannot resurrect closed work.
    """
    inventory = build_issue_work_inventory(portfolio_snapshot)
    disposition_path = state_root / "issue-dispositions.json"
    try:
        durable = json.loads(disposition_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        durable = {}
    if not isinstance(durable, dict):
        durable = {}
    reconciled = merge_issue_dispositions(inventory["work_items"], durable)
    actionable = [row for row in reconciled if row.get("eligible") is True and not row.get("blocked") and not row.get("owned")]
    payload = {
        "schema_version": 1,
        "status": "ACTIONABLE_ISSUES" if actionable else "NO_ACTIONABLE_ISSUES",
        "work_items": reconciled,
        "ranked_actionable": sorted(actionable, key=lambda row: (
            -int(row.get("usability_impact", 0)),
            -int(row.get("severity", 0)),
            -int(row.get("roadmap_unlock", 0)),
            str(row.get("work_id") or ""),
        )),
        "issue_debt": inventory["issue_debt"],
        "recorded_at_utc": datetime.now(UTC).isoformat(),
    }
    atomic_json(state_root / "issue-work-frontier.json", payload)
    return payload


def reconcile_issue_cycle(state_root: Path) -> dict:
    snapshot_path = state_root / "github-portfolio-snapshot.json"
    try:
        return reconcile_issue_frontier(
            json.loads(snapshot_path.read_text(encoding="utf-8")), state_root
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        result = {"status": "ISSUE_EVIDENCE_UNAVAILABLE", "reason": str(exc)}
        atomic_json(
            state_root / "issue-work-frontier.json",
            {
                "schema_version": 1,
                **result,
                "recorded_at_utc": datetime.now(UTC).isoformat(),
            },
        )
        return result
