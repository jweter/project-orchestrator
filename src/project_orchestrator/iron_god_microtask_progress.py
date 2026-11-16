"""Durable *observational* progress receipts for local microtask plans.

The planner/worker/backlog remain the sole execution authorities. A receipt is
a recovery diagnostic, never a substitute for a GitHub merge, current approved
roadmap, owner control, exact-head preflight, or native product acceptance.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .state_io import atomic_json

PROGRESS_DIR = "microtask-progress"
SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(UTC).isoformat()


def checkpoint_path(lane_root: Path, plan: dict[str, Any]) -> Path:
    """Separate revisions of a plan: old evidence cannot be mistaken for a new plan."""
    parent = str(plan["parent_id"])
    identity = {
        "parent_id": parent,
        "planned_at_head": plan.get("planned_at_head"),
        "roadmap_ref": plan.get("roadmap_ref"),
        "subtasks": plan["subtasks"],
        "coverage": plan.get("coverage"),
    }
    revision = hashlib.sha256(
        json.dumps(identity, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    parent_hash = hashlib.sha256(parent.encode("utf-8")).hexdigest()[:10]
    safe = re.sub(r"[^a-zA-Z0-9_-]", "-", parent)[:40] or "parent"
    return Path(lane_root) / PROGRESS_DIR / f"{safe}-{parent_hash}-{revision}.json"


def checkpoint_replay_conflicts(
    lane_root: Path,
    plan: dict[str, Any],
    items: list[dict[str, Any]],
    state: dict[str, Any],
) -> list[str]:
    """Fail closed when restart state contradicts an already-observed child receipt.

    The canonical backlog/ledger alone decides what is DONE and what may be
    dispatched. Historical checkpoint receipts are never used to *grant*
    completion or a new worker. They can, however, veto replay when a
    checkpoint says a child was merged or accepted locally but the canonical
    state/approved item needed to reconcile it has vanished.

    Caller must leave the plan ACTIVE for reconciliation: do not reset lineage
    budgets, create a new queue or infer a live owner/lease from this history.
    """
    path = checkpoint_path(lane_root, plan)
    # A dangling symlink is not "no checkpoint": never overwrite its unknown
    # historical target with a fresh empty snapshot during a refill pass.
    if path.is_symlink():
        return ["CHECKPOINT_INVALID_FILE"]
    if not path.exists():
        return []
    try:
        if not path.is_file() or path.stat().st_size > 60_000:
            return ["CHECKPOINT_INVALID_FILE"]
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return ["CHECKPOINT_UNREADABLE"]
    if (
        not isinstance(saved, dict)
        or saved.get("schema_version") != SCHEMA_VERSION
        or saved.get("parent_id") != plan.get("parent_id")
        or saved.get("repository") != plan.get("repository")
        or saved.get("planned_at_head") != plan.get("planned_at_head")
        or saved.get("product_acceptance") != "UNVERIFIED_BY_THIS_CHECKPOINT"
        or not isinstance(saved.get("receipts"), list)
    ):
        return ["CHECKPOINT_IDENTITY_UNVERIFIED"]
    known = {
        item["id"] for item in items
        if (isinstance(item, dict) and isinstance(item.get("id"), str)
            and item.get("repository") == plan.get("repository"))
    }
    rows = state.get("items") if isinstance(state, dict) else None
    rows = rows if isinstance(rows, dict) else {}
    keys = {
        sub.get("key") for sub in plan.get("subtasks", [])
        if isinstance(sub, dict) and isinstance(sub.get("key"), str)
    }
    conflicts: list[str] = []
    if len(saved["receipts"]) > 1000:
        return ["CHECKPOINT_RECEIPTS_UNVERIFIED"]
    for receipt in saved["receipts"]:
        if not isinstance(receipt, dict):
            return ["CHECKPOINT_RECEIPTS_UNVERIFIED"]
        kind = receipt.get("receipt")
        if not isinstance(kind, str):
            return ["CHECKPOINT_RECEIPTS_UNVERIFIED"]
        if kind not in {"GITHUB_MERGED_IN_LANE_LEDGER", "LOCAL_ACCEPTED_ONLY"}:
            continue
        key = receipt.get("key")
        ident = receipt.get("attempt_id")
        if not isinstance(key, str) or not isinstance(ident, str) or key not in keys:
            return ["CHECKPOINT_RECEIPT_IDENTITY_UNVERIFIED"]
        base = f"{plan['parent_id']}-{key}"
        if not (ident == base or
                (ident.startswith(base + "-r") and ident[len(base) + 2:].isdigit())):
            return ["CHECKPOINT_RECEIPT_IDENTITY_UNVERIFIED"]
        row = rows.get(ident)
        if ident not in known or not isinstance(row, dict):
            conflicts.append(key)
        elif kind == "GITHUB_MERGED_IN_LANE_LEDGER" and row.get("outcome") != "MERGED":
            conflicts.append(key)
    return sorted(set(conflicts))


def filter_replay_conflicted_items(
    lane_root: Path,
    approved: list[dict[str, Any]],
    state: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Veto unsafe *existing* approved items before the actual Product dispatch.

    This is a negative-only guard: checkpoint history cannot add approved
    items or mark any item done. If any task receipt in an ACTIVE plan cannot
    be reconciled with canonical approved items and backlog-state, withhold
    that parent's existing child items. Independent plans stay eligible.
    """
    from . import iron_god_task_decomposer as decomposer

    blocked_ids: set[str] = set()
    for plan in decomposer.load_plans(lane_root):
        if plan.get("status") != "ACTIVE" or not checkpoint_replay_conflicts(
            lane_root, plan, approved, state
        ):
            continue
        repo = plan.get("repository")
        for sub in plan["subtasks"]:
            if not isinstance(sub, dict) or not isinstance(sub.get("key"), str):
                continue
            base = f"{plan['parent_id']}-{sub['key']}"
            for item in approved:
                if not isinstance(item, dict) or item.get("repository") != repo:
                    continue
                ident = item.get("id")
                if (isinstance(ident, str) and
                        (ident == base or
                         (ident.startswith(base + "-r") and
                          ident[len(base) + 2:].isdigit()))):
                    blocked_ids.add(ident)
    return ([item for item in approved
             if isinstance(item, dict) and item.get("id") not in blocked_ids],
            sorted(blocked_ids))


def _current_subtasks(
    lane_root: Path,
    plan: dict[str, Any],
    items: list[dict[str, Any]],
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    from . import iron_god_roadmap_refill as refill
    from . import iron_god_task_decomposer as decomposer

    outcomes = state.get("items") or {}
    rows = []
    for sub in plan["subtasks"]:
        base = f"{plan['parent_id']}-{sub['key']}"
        status, candidate_id = refill._slice_status(base, items, state)
        # FAILED returns the *future* revision id (e.g. -r1), which has no
        # outcome yet. Preserve the last existing revision as the observed
        # attempt and report the candidate separately for recovery diagnostics.
        revisions = refill.revisions(base, items) if status == "FAILED" else []
        attempt_id = str(revisions[-1]["id"]) if revisions else candidate_id
        if status in {"NEW", "FAILED"} and decomposer.drafting_exhausted(
            lane_root, candidate_id, directed=sub.get("kind") == "edit"
        ):
            status = "EXHAUSTED"
        attempt = outcomes.get(attempt_id) if isinstance(outcomes, dict) else None
        attempt = attempt if isinstance(attempt, dict) else {}
        outcome = str(attempt.get("outcome") or "")
        packets = attempt.get("packets")
        packet_ids = (
            [str(p)[:128] for p in packets if isinstance(p, str)][:4]
            if isinstance(packets, list)
            else []
        )
        if (
            isinstance(attempt.get("packet_id"), str)
            and attempt["packet_id"] not in packet_ids
        ):
            packet_ids.append(attempt["packet_id"][:128])

        receipt = (
            "GITHUB_MERGED_IN_LANE_LEDGER"
            if status == "DONE" and outcome == "MERGED"
            else "LOCAL_ACCEPTED_ONLY"
            if outcome == "ACCEPTED_LOCAL"
            else "NO_VERIFIED_COMPLETION_RECEIPT"
        )
        rows.append(
            {
                "key": sub["key"],
                "depends_on": list(sub.get("depends_on") or []),
                "attempt_id": attempt_id,
                "next_attempt_id": candidate_id if candidate_id != attempt_id else None,
                "status": status,
                "outcome": outcome[:120] or None,
                "attempts": attempt.get("attempts")
                if type(attempt.get("attempts")) is int
                else None,
                "packet_ids": packet_ids,
                "receipt": receipt,
            }
        )
    return rows


def repair_candidates(
    plan: dict[str, Any], rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Locate the smallest unfinished local branches without issuing new attempts.

    These are *diagnostic* manifests, never admission or continuation grants.
    The existing lineage cap remains authoritative even if the failed work
    could be split into a smaller, newly approved roadmap slice later.
    """
    by_key = {row["key"]: row for row in rows}
    completed = [row["key"] for row in rows if row["status"] == "DONE"]
    coverage = plan.get("coverage") or {}
    original = plan.get("slice_acceptance") or []
    candidates: list[dict[str, Any]] = []
    for sub in plan["subtasks"]:
        key = sub["key"]
        row = by_key.get(key, {})
        if row.get("status") != "EXHAUSTED":
            continue

        impacted = {key}
        for descendant in plan["subtasks"]:
            descendant_key = descendant["key"]
            if by_key.get(descendant_key, {}).get("status") == "DONE":
                continue
            if any(dep in impacted for dep in descendant.get("depends_on") or []):
                impacted.add(descendant_key)
        criteria = []
        for number, owners in coverage.items():
            if not isinstance(owners, list) or not any(k in impacted for k in owners):
                continue
            if not str(number).isdigit() or not 1 <= int(number) <= len(original):
                continue
            criteria.append(
                {"index": int(number), "text": str(original[int(number) - 1])[:300]}
            )
        scope = [sub.get("module_path"), sub.get("test_path")]
        candidates.append(
            {
                "kind": "EXHAUSTED_LOCAL_MICROTASK",
                "failed_key": key,
                "observed_attempt_id": row.get("attempt_id"),
                "blocked_keys": [
                    s["key"] for s in plan["subtasks"] if s["key"] in impacted
                ],
                "already_completed_keys": completed,
                "affected_owner_criteria": sorted(criteria, key=lambda c: c["index"]),
                "narrow_existing_paths": [p for p in scope if isinstance(p, str) and p],
                "outcome": row.get("outcome"),
                "attempts": row.get("attempts"),
                "packet_ids": list(row.get("packet_ids") or []),
                "proposed_action": "PROPOSE_SMALLER_ROADMAP_REPAIR_FOR_OWNER_APPROVAL",
                "requires_new_authorization": True,
                "dispatchable": False,
                "attempt_budget_reset": False,
            }
        )
    if plan.get("status") == "PARTIAL" and plan.get("residual"):
        candidates.append(
            {
                "kind": "UNDELIVERED_RESIDUAL",
                "remaining_requirements": [str(r)[:300] for r in plan["residual"][:5]],
                "already_completed_keys": completed,
                "proposed_action": "PROPOSE_RESIDUAL_ROADMAP_SLICE_FOR_OWNER_APPROVAL",
                "requires_new_authorization": True,
                "dispatchable": False,
                "attempt_budget_reset": False,
            }
        )
    return candidates


def record_checkpoints(
    lane_root: Path,
    repository: str,
    head: str,
    items: list[dict[str, Any]],
    state: dict[str, Any],
) -> list[str]:
    """Project canonical lane state into durable, idempotent per-plan progress records.

    Only call from the normal authorized lane pass after the existing settle()
    operation. This function does not dispatch, write repository files, or
    reauthorize exhausted plans. Historical receipts are diagnostic only.
    """
    from . import iron_god_task_decomposer as decomposer

    changed = []
    for plan in decomposer.load_plans(lane_root):
        if plan.get("repository") != repository or not plan["subtasks"]:
            continue
        path = checkpoint_path(lane_root, plan)
        # Refill runs BEFORE child_requests. Reject malformed or unreadable
        # checkpoint evidence rather than overwrite it with empty receipts.
        # For a VALID checkpoint whose historical receipt contradicts missing
        # canonical state, preserve receipts while refreshing observational
        # subtasks; the later replay veto still prevents dispatch.
        conflicts = checkpoint_replay_conflicts(lane_root, plan, items, state)
        if any(problem.startswith("CHECKPOINT_") for problem in conflicts):
            continue
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(previous, dict):
                previous = {}
        except (OSError, ValueError):
            previous = {}

        rows = _current_subtasks(lane_root, plan, items, state)
        stored_receipts = previous.get("receipts")
        receipts = (
            [r for r in stored_receipts if isinstance(r, dict)]
            if isinstance(stored_receipts, list)
            else []
        )
        for row in rows:
            if row["receipt"] == "NO_VERIFIED_COMPLETION_RECEIPT":
                continue
            identity = (row["key"], row["attempt_id"], row["receipt"])
            if any(
                (r.get("key"), r.get("attempt_id"), r.get("receipt")) == identity
                for r in receipts
                if isinstance(r, dict)
            ):
                continue
            receipts.append(
                {
                    "key": row["key"],
                    "attempt_id": row["attempt_id"],
                    "receipt": row["receipt"],
                    "packet_ids": row["packet_ids"],
                    "recorded_at_utc": _now(),
                }
            )

        snapshot = {
            "schema_version": SCHEMA_VERSION,
            "advisory_only": True,
            "product_acceptance": "UNVERIFIED_BY_THIS_CHECKPOINT",
            "parent_id": plan["parent_id"],
            "repository": repository,
            "roadmap_ref": plan.get("roadmap_ref"),
            "planned_at_head": plan.get("planned_at_head"),
            "observed_repository_head": head,
            "plan_status": plan["status"],
            "subtasks": rows,
            "repair_candidates": repair_candidates(plan, rows),
            "receipts": receipts,
        }
        if (
            all(previous.get(key) == value for key, value in snapshot.items())
            and previous
        ):
            continue
        atomic_json(path, {**snapshot, "checkpointed_at_utc": _now()})
        changed.append(plan["parent_id"])
    return changed
