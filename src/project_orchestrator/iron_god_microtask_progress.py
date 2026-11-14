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
