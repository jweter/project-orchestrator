"""Owner-gated local Ollama repair advice from the *current* recorded lane checkpoint.

Read-only against roadmap/backlog/working trees. A newly observed, exhausted
child gets at most one durable advisory proposal per identical evidence set.
No child is reauthorized, no attempt cap is reset, and no supervisor is started.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from . import iron_god_microtask_progress as progress
from . import iron_god_microtask_repair_outline as outline
from . import iron_god_task_decomposer as decomposer
from .state_io import atomic_json

ADVICE_DIR = "microtask-repair-advice"
_VALID_PARENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_VALID_SHA = re.compile(r"[0-9a-f]{40}\Z")


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _load_current(
    lane_root: Path, parent_id: str, repository: str, head: str, failed_key: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bytes, bytes] | None:
    """Match source plan + specific exhausted child + same-head checkpoint.

    A checkpoint is evidence, *not* execution authority. This read does not
    consume a backlog item or infer its owner grant from a plan.
    """
    if not _VALID_PARENT.fullmatch(parent_id) or not _VALID_SHA.fullmatch(head):
        return None
    path = decomposer.plan_path(lane_root, parent_id)
    try:
        plan_bytes = path.read_bytes()
        plan = json.loads(plan_bytes)
    except (OSError, ValueError):
        return None
    if (
        not isinstance(plan, dict)
        or plan.get("parent_id") != parent_id
        or plan.get("repository") != repository
        or plan.get("status") != "BLOCKED"
        or not isinstance(plan.get("subtasks"), list)
    ):
        return None
    checkpoint_path = progress.checkpoint_path(lane_root, plan)
    try:
        checkpoint_bytes = checkpoint_path.read_bytes()
        checkpoint = json.loads(checkpoint_bytes)
    except (OSError, ValueError):
        return None
    if (
        not isinstance(checkpoint, dict)
        or checkpoint.get("advisory_only") is not True
        or checkpoint.get("parent_id") != parent_id
        or checkpoint.get("repository") != repository
        or checkpoint.get("plan_status") != "BLOCKED"
        or checkpoint.get("observed_repository_head") != head
        or not isinstance(checkpoint.get("repair_candidates"), list)
        or not isinstance(checkpoint.get("subtasks"), list)
    ):
        return None
    candidates = [
        c
        for c in checkpoint["repair_candidates"]
        if isinstance(c, dict)
        and c.get("kind") == "EXHAUSTED_LOCAL_MICROTASK"
        and c.get("failed_key") == failed_key
    ]
    rows = [
        row
        for row in checkpoint["subtasks"]
        if isinstance(row, dict)
        and row.get("key") == failed_key
        and row.get("status") == "EXHAUSTED"
    ]
    if len(candidates) != 1 or len(rows) != 1:
        return None
    candidate, row = candidates[0], rows[0]
    if (
        candidate.get("observed_attempt_id") != row.get("attempt_id")
        or candidate.get("outcome") != row.get("outcome")
        or candidate.get("packet_ids") != row.get("packet_ids")
        or candidate.get("attempts") != row.get("attempts")
    ):
        return None
    # Validate before model time. The later model output is checked again.
    if outline._original_scope(plan, candidate)[-1]:
        return None
    return plan, checkpoint, candidate, plan_bytes, checkpoint_bytes


def proposal_path(
    lane_root: Path,
    plan: dict[str, Any],
    candidate: dict[str, Any],
    head: str,
    checkpoint: dict[str, Any],
) -> Path:
    """Content-addressed, including current ledger receipts and checkpoint."""
    fingerprint = _digest(
        {
            "plan": plan,
            "candidate": candidate,
            "head": head,
            "checkpoint": checkpoint,
        }
    )
    return Path(lane_root) / ADVICE_DIR / f"{fingerprint}.json"


def propose_current(
    lane_root: Path,
    parent_id: str,
    repository: str,
    head: str,
    failed_key: str,
    *,
    authority: Callable[[], bool],
    ask_local: Callable[[str], Any],
    model_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create one durable, non-dispatchable proposal while owner authority holds.

    This is a one-shot, separately invoked advisory step—not an engine grant.
    A crash may leave the exclusive lock: fail closed for manual reconciliation,
    instead of replaying paid/unverified work in another process.
    """
    if not authority():
        return {"status": "OWNER_AUTHORITY_DENIED"}
    lane_root = Path(lane_root)
    source = _load_current(lane_root, parent_id, repository, head, failed_key)
    if source is None:
        return {"status": "NO_CURRENT_EXHAUSTED_CANDIDATE"}
    plan, checkpoint, candidate, plan_bytes, checkpoint_bytes = source
    path = proposal_path(lane_root, plan, candidate, head, checkpoint)
    input_digest = path.stem
    if path.is_file():
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
            if (
                result.get("input_sha256") == input_digest
                and result.get("advisory_only") is True
            ):
                return {
                    "status": "ALREADY_RECORDED",
                    "path": str(path),
                    "record": result,
                }
        except (OSError, ValueError, AttributeError):
            pass
        return {"status": "ADVICE_RECORD_UNVERIFIED"}

    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(".lock")
    try:
        handle = os.open(str(lock), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return {"status": "ADVICE_LOCK_HELD_RECONCILE_MANUALLY"}
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(f"pid={os.getpid()}\n")
        if path.exists():
            return {"status": "ADVICE_RECORD_UNVERIFIED"}

        def guarded_ask(prompt: str) -> Any:
            if not authority():
                raise RuntimeError("EXECUTION_AUTHORITY_REVOKED")
            return ask_local(prompt)

        attempt = outline.propose(plan, candidate, guarded_ask)
        if not authority():
            return {"status": "OWNER_AUTHORITY_DENIED_AFTER_MODEL"}
        # Detect checkpoint/head or source-plan changes while local Ollama was busy.
        current = _load_current(lane_root, parent_id, repository, head, failed_key)
        if (
            current is None
            or current[3] != plan_bytes
            or current[4] != checkpoint_bytes
        ):
            return {"status": "SOURCE_OR_CHECKPOINT_CHANGED"}
        if not attempt.get("ok") and str(attempt.get("reason", "")).startswith(
            "LOCAL_MODEL_UNAVAILABLE"
        ):
            return {
                "status": "LOCAL_MODEL_UNAVAILABLE",
                "reason": attempt.get("reason"),
            }

        record = {
            "schema_version": 1,
            "advisory_only": True,
            "requires_new_authorization": True,
            "dispatchable": False,
            "attempt_budget_reset": False,
            "product_acceptance": "UNVERIFIED",
            "parent_id": parent_id,
            "repository": repository,
            "observed_head": head,
            "failed_key": failed_key,
            "observed_attempt_id": candidate["observed_attempt_id"],
            "input_sha256": input_digest,
            "checkpoint_sha256": hashlib.sha256(checkpoint_bytes).hexdigest(),
            "model_provenance": model_provenance
            or {"provider": "LOCAL_OLLAMA_REQUESTED"},
            "outcome": attempt,
            "created_at_utc": datetime.now(UTC).isoformat(),
        }
        if not authority():
            return {"status": "OWNER_AUTHORITY_DENIED_AFTER_MODEL"}
        atomic_json(path, record)
        return {"status": "ADVISORY_RECORDED", "path": str(path), "record": record}
    finally:
        lock.unlink(missing_ok=True)
