"""Fail-closed repair-advice -> backlog-schema review package.

This is deliberately NOT an importer: a capped lineage never gets fresh
attempts just because a local model proposed a different implementation.
Only explicit later owner validation may create an approved canonical item.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from . import iron_god_backlog as backlog
from . import iron_god_microtask_repair_advisor as advisor
from . import iron_god_microtask_repair_outline as outline
from .state_io import atomic_json

INBOX_DIR = "microtask-repair-intake"
HEX_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _prepare(
    lane_root: Path,
    advice_sha: str,
    repository: str,
    head: str,
    items: list[dict[str, Any]],
    state: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    """Revalidate every untrusted handoff against original source and ledger."""
    if not HEX_SHA.fullmatch(advice_sha):
        return None, "INVALID_ADVICE_IDENTIFIER"
    if (
        not isinstance(items, list)
        or not isinstance(state, dict)
        or not isinstance(state.get("items"), dict)
    ):
        return None, "BACKLOG_CONTEXT_UNVERIFIED"
    path = lane_root / advisor.ADVICE_DIR / f"{advice_sha}.json"
    if path.is_symlink():
        return None, "ADVICE_SYMLINK_UNTRUSTED"
    try:
        advice_bytes = path.read_bytes()
        advice = json.loads(advice_bytes)
    except (OSError, ValueError):
        return None, "ADVICE_UNREADABLE"
    if not isinstance(advice, dict):
        return None, "ADVICE_NOT_AN_OBJECT"
    if (
        advice.get("advisory_only") is not True
        or advice.get("requires_new_authorization") is not True
        or advice.get("dispatchable") is not False
        or advice.get("attempt_budget_reset") is not False
        or advice.get("product_acceptance") != "UNVERIFIED"
        or advice.get("input_sha256") != advice_sha
        or advice.get("repository") != repository
        or advice.get("observed_head") != head
    ):
        return None, "ADVICE_AUTHORITY_OR_IDENTITY_MISMATCH"
    parent = advice.get("parent_id")
    failed_key = advice.get("failed_key")
    if not isinstance(parent, str) or not isinstance(failed_key, str):
        return None, "ADVICE_PARENT_OR_CHILD_INVALID"
    source = advisor._load_current(lane_root, parent, repository, head, failed_key)
    if source is None:
        return None, "SOURCE_NOT_CURRENT_OR_EXHAUSTED"
    plan, checkpoint, candidate, plan_bytes, checkpoint_bytes = source
    if (
        advisor.proposal_path(lane_root, plan, candidate, head, checkpoint) != path
        or advice.get("checkpoint_sha256") != _sha256(checkpoint_bytes)
        or advice.get("observed_attempt_id") != candidate.get("observed_attempt_id")
    ):
        return None, "ADVICE_EVIDENCE_MISMATCH"

    attempt_id = candidate["observed_attempt_id"]
    recorded_attempt = state["items"].get(attempt_id)
    if (
        not isinstance(recorded_attempt, dict)
        or recorded_attempt.get("outcome") != candidate.get("outcome")
        or recorded_attempt.get("attempts") != candidate.get("attempts")
    ):
        return None, "CANONICAL_FAILED_ATTEMPT_MISSING_OR_CHANGED"
    canonical_packets = (
        set(p for p in recorded_attempt.get("packets") or [] if isinstance(p, str))
        if isinstance(recorded_attempt.get("packets"), list)
        else set()
    )
    if isinstance(recorded_attempt.get("packet_id"), str):
        canonical_packets.add(recorded_attempt["packet_id"])
    if any(
        not isinstance(p, str) or p not in canonical_packets
        for p in candidate.get("packet_ids") or []
    ):
        return None, "CANONICAL_PACKET_EVIDENCE_MISSING_OR_CHANGED"
    if candidate.get("attempt_budget_reset") is not False:
        return None, "ATTEMPT_RESET_FORBIDDEN"
    outcome = advice.get("outcome")
    if not isinstance(outcome, dict) or outcome.get("ok") is not True:
        return None, "NO_VALIDATED_LOCAL_REPAIR"
    proposed = outcome.get("proposal")
    normalized, problems = outline.validate_outline(plan, candidate, proposed)
    if problems or normalized != proposed:
        return None, "REPAIR_OUTLINE_NOT_CANONICAL"

    original, _paths, owned_criteria, scope_errors = outline._original_scope(
        plan, candidate
    )
    if original is None or scope_errors:
        return None, "REPAIR_OUTLINE_SCOPE_CHANGED"
    code_path = original["module_path"]
    test_path = original.get("test_path")
    # A new module may need a Level 1 build; an existing module may require
    # Level 2 red tests and named units. This remains an UNROUTED proposal.
    suggested_level = 2 if original.get("kind") == "edit" else 1
    proposed_id = "IGR-" + advice_sha[:20]

    if any(
        not isinstance(item, dict) or item.get("id") == proposed_id for item in items
    ):
        return None, "BACKLOG_ID_COLLISION_OR_MALFORMED_ITEM"
    reserved = []
    for item in items:
        if item.get("repository") != repository or item.get("status") != "approved":
            continue
        row = state["items"].get(item.get("id"), {})
        # This failed attempt's own pre-existing lineage is not newly
        # authorized work. Other reservations still preclude staging.
        if (
            backlog.lineage_base(str(item.get("id")))
            == backlog.lineage_base(str(attempt_id))
            and isinstance(row, dict)
            and str(row.get("outcome") or "").startswith("FAIL")
        ):
            continue
        finished = isinstance(row, dict) and (
            row.get("outcome") == "MERGED"
            or str(row.get("outcome") or "").startswith(backlog.EXTERNAL)
        )
        if code_path in backlog.edit_files(item) and not finished:
            reserved.append(str(item["id"]))
    if reserved:
        return None, "EXISTING_APPROVED_WORK_OWNS_TARGET"

    acceptance = [
        str(plan["slice_acceptance"][number - 1]) for number in owned_criteria
    ]
    objective = "; ".join(step["objective"] for step in normalized["steps"])[:750]
    item = {
        "id": proposed_id,
        "repository": repository,
        "title": f"REVIEW ONLY: alternative for exhausted {failed_key} in {parent}"[
            :120
        ],
        # This MUST NOT match OWNER_APPROVED_ROADMAP_*.md: that promotion
        # path could otherwise turn the local proposal into approved work.
        "roadmap_ref": f"REPAIR_REVIEW:{parent}:{failed_key}"[:300],
        "addresses": f"{parent} / {failed_key}: original owner-approved acceptance",
        "why": f"Existing lineage {attempt_id} exhausted, recorded outcome {candidate['outcome']}. A separate authorized strategy may be required."[
            :800
        ],
        "consumer": f"the existing {repository} feature defined by parent {parent}",
        "objective": objective,
        "interfaces": [code_path],
        "depends_on": [],
        "acceptance_criteria": acceptance,
        "tests": [test_path] if isinstance(test_path, str) and test_path else [],
        "level": suggested_level,
        "complexity": "low",
        "verification": "Review a real failing approved test at the exact source head before any new build; enforce native acceptance and CI.",
        "definition_of_done": "Only a separately authorized backlog item merged with required exact-head CI, review and product tests.",
        "status": "proposed",
        "repair_origin": {
            "parent_id": parent,
            "failed_key": failed_key,
            "failed_attempt_id": attempt_id,
            "already_completed_keys": list(candidate["already_completed_keys"]),
            "blocked_keys": list(candidate["blocked_keys"]),
            "source_roadmap_ref": plan.get("roadmap_ref"),
            "advice_sha256": advice_sha,
            "original_plan_sha256": _sha256(plan_bytes),
            "original_checkpoint_sha256": _sha256(checkpoint_bytes),
            "lineage_attempts_remain_spent": True,
        },
    }
    if backlog.item_problems(item):
        return None, "PROPOSED_ITEM_SCHEMA_INVALID"
    if not backlog.item_problems(
        {**item, "status": "approved", "assignee": "iron-god"}
    ):
        return None, "ACCIDENTALLY_DISPATCHABLE_ON_STATUS_FLIP"
    # Note that no approved build/direct spec, red-test oracle, or dispatch
    # token appears here. These must come from a separate verification gate.
    return {
        "schema_version": 1,
        "advisory_only": True,
        "review_required": True,
        "dispatchable": False,
        "ready_for_backlog_import": False,
        "ready_for_automatic_roadmap_promotion": False,
        "attempt_budget_reset": False,
        "advice_sha256": advice_sha,
        "advice_file_sha256": _sha256(advice_bytes),
        "owner_review_checklist": [
            "Confirm this is a genuinely different objective, not another attempt in the capped lineage",
            "Verify original owner-approved roadmap and exact Git HEAD",
            "Review already merged sibling evidence and existing file reservations",
            "Supply separately reviewed concrete build or directed-edit specification and actual failing test",
            "Apply owner-control, local worker, native product, preflight, CI and release gates before executing",
        ],
        "review_item": item,
        "steps": normalized["steps"],
    }, ""


def stage_for_review(
    lane_root: Path,
    advice_sha: str,
    repository: str,
    head: str,
    *,
    items: list[dict[str, Any]],
    state: dict[str, Any],
    authority: Callable[[], bool],
) -> dict[str, Any]:
    """Persist an immutable review-only package, never a live backlog item."""
    if not authority():
        return {"status": "OWNER_AUTHORITY_DENIED"}
    lane_root = Path(lane_root)
    package, reason = _prepare(lane_root, advice_sha, repository, head, items, state)
    if package is None:
        return {"status": reason}
    path = lane_root / INBOX_DIR / f"{advice_sha}.json"
    if path.is_symlink():
        return {"status": "INTAKE_RECORD_UNVERIFIED"}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and existing.get("package") == package:
                return {"status": "ALREADY_STAGED", "path": str(path)}
        except (OSError, ValueError):
            pass
        return {"status": "INTAKE_RECORD_UNVERIFIED"}
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(".lock")
    try:
        handle = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return {"status": "INTAKE_LOCK_HELD_RECONCILE"}
    try:
        os.close(handle)
        if not authority():
            return {"status": "OWNER_AUTHORITY_DENIED"}
        latest, reason = _prepare(lane_root, advice_sha, repository, head, items, state)
        if latest != package or reason:
            return {"status": "INTAKE_SOURCE_CHANGED"}
        if path.exists():
            return {"status": "INTAKE_RECORD_UNVERIFIED"}
        record = {"package": package, "staged_at_utc": datetime.now(UTC).isoformat()}
        atomic_json(path, record)
        return {
            "status": "REVIEW_PACKAGE_STAGED",
            "path": str(path),
            "package": package,
        }
    finally:
        lock.unlink(missing_ok=True)
