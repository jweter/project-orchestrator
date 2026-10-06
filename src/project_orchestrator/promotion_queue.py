from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .flow_signal import build_stale_promotion_recovery
from .reconciliation_promotion import evaluate_promotion, reconcile_packet
from .state_io import atomic_json
from .throughput_guard import classify_promotion_failure, release_active_work_lease

SCHEMA_VERSION = 1

PROMOTION_QUEUE_STATES = {
    "CI_PENDING",
    "REVIEW_PENDING",
    "MERGE_READY",
    "MERGE_ENQUEUED",
    "MERGED",
    "REPAIR_REQUIRED",
    "BLOCKED",
}
TERMINAL_STATES = {"MERGED"}

DEFAULT_BASE_BACKOFF_SECONDS = 60
DEFAULT_MAX_BACKOFF_SECONDS = 3600
DEFAULT_MERGE_READY_STALE_AFTER_SECONDS = 900

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# Only a genuinely transient, non-code outcome is safe to replay against the same
# head; everything else must route through a human-meaningful queue state so a
# promotion failure is never silently retried into a false GREEN.
_MERGE_FAILURE_ROUTES = {
    "CODE_FAILURE": "REPAIR_REQUIRED",
    "TEST_FAILURE": "REPAIR_REQUIRED",
    "QUALITY_FAILURE": "REPAIR_REQUIRED",
    "REVIEW_FAILURE": "REVIEW_PENDING",
    "POLICY_FAILURE": "BLOCKED",
    "PRODUCT_REALITY_PENDING": "BLOCKED",
    "PLATFORM_GATE": "MERGE_READY",
    "ENVIRONMENT_FAILURE": "MERGE_READY",
    "UNKNOWN": "BLOCKED",
}

# evaluate_promotion() BLOCKED reasons that are not routed through classify_promotion_failure.
_BLOCKED_REASON_ROUTES = {
    "MERGEABILITY_NOT_GREEN": "REPAIR_REQUIRED",
    "REQUIRED_REVIEWS_NOT_SATISFIED": "REVIEW_PENDING",
    "UNRESOLVED_REVIEW_THREADS": "REVIEW_PENDING",
    "OWNERSHIP_LEASE_NOT_VALID": "BLOCKED",
    "POLICY_GATE_NOT_CLEAR": "BLOCKED",
    "RECONCILIATION_NOT_COMPLETE": "BLOCKED",
}


class PromotionQueueError(ValueError):
    pass


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PromotionQueueError(f"{name} must be a non-empty string")
    return value.strip()


def _sha(value: Any, name: str = "head_sha") -> str:
    text = _nonempty(value, name)
    if not _SHA_RE.fullmatch(text):
        raise PromotionQueueError(f"{name} must be a 40-character lowercase hex SHA")
    return text


def _pr_number(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PromotionQueueError("pr_number must be a positive integer or null")
    return value


def _paths(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise PromotionQueueError("changed_paths must be a string list")
    return sorted(set(value))


def _parse_time(value: Any) -> datetime:
    text = _nonempty(value, "timestamp")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise PromotionQueueError("timestamps must include a timezone")
    return parsed


def entry_key(repository: str, *, pr_number: int | None, head_sha: str) -> str:
    """Idempotent identity: PR identity when known, otherwise the exact head.

    A PR identity is stable across force-pushes/new commits, so once a PR number
    exists the same entry tracks every head the PR ever points to. Before a PR
    exists, the exact head is itself the only durable identity.
    """
    repository = _nonempty(repository, "repository")
    head_sha = _sha(head_sha)
    number = _pr_number(pr_number)
    identity: dict[str, Any] = {"repository": repository, "pr_number": number}
    if number is None:
        identity["head_sha"] = head_sha
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


def _event(kind: str, at: str, detail: str) -> dict[str, Any]:
    return {"kind": kind, "at": at, "detail": detail}


def _initial_retry(now: str) -> dict[str, Any]:
    return {"attempt": 0, "backoff_seconds": 0, "next_eligible_at": now}


def _next_retry(
    now: str,
    attempt: int,
    *,
    base: int = DEFAULT_BASE_BACKOFF_SECONDS,
    cap: int = DEFAULT_MAX_BACKOFF_SECONDS,
) -> dict[str, Any]:
    backoff = min(cap, base * (2 ** (attempt - 1))) if attempt > 0 else 0
    eligible = _parse_time(now) + timedelta(seconds=backoff)
    return {"attempt": attempt, "backoff_seconds": backoff, "next_eligible_at": eligible.isoformat()}


def is_eligible_for_dispatch(entry: dict[str, Any], *, now: str) -> bool:
    """Workers consult this instead of sleeping; an ineligible entry is simply skipped this cycle."""
    next_eligible = entry.get("retry", {}).get("next_eligible_at")
    if not next_eligible:
        return True
    return _parse_time(now) >= _parse_time(next_eligible)


def dispatch_eligible_entries(
    queue: dict[str, Any],
    *,
    now: str,
    states: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return non-terminal, backoff-eligible entries in a deterministic order.

    This is the only interface a polling worker needs; it never sleeps and never
    blocks, it just reports what is safe to act on *this* cycle.
    """
    queue = validate_queue(queue)
    wanted = set(states) if states else None
    eligible = [
        entry
        for entry in queue["entries"].values()
        if entry["state"] not in TERMINAL_STATES
        and (wanted is None or entry["state"] in wanted)
        and is_eligible_for_dispatch(entry, now=now)
    ]
    eligible.sort(key=lambda entry: (entry["state"], entry["entry_id"]))
    return eligible


def empty_queue() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "entries": {}}


def validate_queue(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise PromotionQueueError("promotion queue schema_version must equal 1")
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        raise PromotionQueueError("promotion queue entries must be an object")
    for key, entry in entries.items():
        if not isinstance(entry, dict) or entry.get("entry_id") != key:
            raise PromotionQueueError("promotion queue entry_id does not match its key")
        if entry.get("state") not in PROMOTION_QUEUE_STATES:
            raise PromotionQueueError("promotion queue entry state is invalid")
    return {"schema_version": SCHEMA_VERSION, "entries": dict(entries)}


def load_queue(path: str | Path) -> dict[str, Any]:
    """Load the durable queue, or start empty when it has never been written."""
    target = Path(path)
    if not target.exists():
        return empty_queue()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PromotionQueueError("promotion queue state file is corrupt") from exc
    return validate_queue(raw)


def write_queue(path: str | Path, queue: dict[str, Any]) -> None:
    """Persist the queue atomically; a crash mid-write never leaves a half-written file."""
    normalized = validate_queue(queue)
    atomic_json(Path(path), normalized)


def _save(queue: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    entries = dict(queue["entries"])
    entries[entry["entry_id"]] = entry
    return {**queue, "entries": entries}


def _transition(
    entry: dict[str, Any],
    state: str,
    now: str,
    *,
    reason: str,
    failure_class: str | None,
    bump_attempt: bool,
    reset_attempts: bool = False,
) -> dict[str, Any]:
    if reset_attempts:
        attempt = 0
    elif bump_attempt:
        attempt = entry["attempts"] + 1
    else:
        attempt = entry["attempts"]
    retry = _next_retry(now, attempt) if (bump_attempt or reset_attempts) else entry["retry"]
    return {
        **entry,
        "state": state,
        "attempts": attempt,
        "retry": retry,
        "failure_class": failure_class,
        "reason": reason,
        "updated_at": now,
        "history": entry["history"] + [_event(state, now, reason)],
    }


def _rebind_stale_head(entry: dict[str, Any], new_head: str, now: str, *, reason: str) -> dict[str, Any]:
    """Invalidate downstream progress and rebind exact-head identity.

    Head movement always resets to CI_PENDING: nothing downstream (review
    approval, mergeability, merge-readiness) survives a changed exact head.
    """
    return {
        **entry,
        "head_sha": new_head,
        "state": "CI_PENDING",
        "retry": _initial_retry(now),
        "failure_class": None,
        "reason": reason,
        "updated_at": now,
        "history": entry["history"] + [_event("STALE_HEAD_INVALIDATED", now, f"{reason}: rebound to {new_head}")],
    }


def enqueue_promotion(
    queue: dict[str, Any],
    *,
    repository: str,
    branch: str,
    base_sha: str,
    head_sha: str,
    changed_paths: list[str],
    now: str,
    pr_number: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Idempotently enqueue a published candidate for the promotion lifecycle.

    This is the worker-release boundary: once a verified candidate is accepted
    here, the caller's product development capacity is free regardless of how
    CI/review/merge later resolve. A repeat call with the same identity and the
    same head is a no-op; a repeat call for the same PR identity at a *different*
    head is a stale-head rebind, never a duplicate entry.
    """
    queue = validate_queue(queue)
    repository = _nonempty(repository, "repository")
    branch = _nonempty(branch, "branch")
    base_sha = _sha(base_sha, "base_sha")
    head_sha = _sha(head_sha)
    paths = _paths(changed_paths)
    number = _pr_number(pr_number)
    _parse_time(now)

    key = entry_key(repository, pr_number=number, head_sha=head_sha)
    existing = queue["entries"].get(key)

    if existing is None:
        entry = {
            "entry_id": key,
            "repository": repository,
            "pr_number": number,
            "branch": branch,
            "base_sha": base_sha,
            "head_sha": head_sha,
            "changed_paths": paths,
            "state": "CI_PENDING",
            "attempts": 0,
            "retry": _initial_retry(now),
            "failure_class": None,
            "reason": "NEWLY_PUBLISHED_CANDIDATE",
            "created_at": now,
            "updated_at": now,
            "history": [_event("ENQUEUED", now, "candidate published; development capacity released")],
        }
        return _save(queue, entry), {"status": "ENQUEUED", "entry": entry}

    if existing["head_sha"] == head_sha and existing.get("pr_number") == number:
        return queue, {"status": "ALREADY_QUEUED", "entry": existing}

    rebound = _rebind_stale_head(
        {**existing, "branch": branch, "base_sha": base_sha, "changed_paths": paths},
        head_sha,
        now,
        reason=f"re-enqueued head moved from {existing['head_sha']}",
    )
    return _save(queue, rebound), {"status": "REBOUND_STALE_HEAD", "entry": rebound}


def _map_promotion_result(promo: dict[str, Any], evidence: dict[str, Any]) -> tuple[str, str, str | None, bool]:
    """Map reconciliation_promotion.evaluate_promotion()'s result onto queue states.

    Returns (status, queue_state, failure_class, bump_attempt). Delegating the
    actual CI/review/mergeability/policy decision to evaluate_promotion means
    this module never re-derives or weakens a merge/security/review/policy gate;
    it only decides *where in the queue* an already-made BLOCKED decision routes.

    Callers must handle promo["state"] == "RECONCILE_REQUIRED" (head movement)
    themselves before reaching here; it always implies a head rebind, not a
    plain queue-state transition.

    evaluate_promotion's internal workflow classification only forwards a few
    evidence fields into classify_promotion_failure, so CODE/TEST/QUALITY
    failure signatures in the caller's full evidence (log_text, jobs_count, ...)
    would never surface through promo["workflow"]. Re-run the same deterministic
    classifier directly on the full evidence to get an accurate failure_class.
    """
    state = promo["state"]
    if state == "CI_PENDING":
        return "CI_PENDING", "CI_PENDING", None, False
    if state == "CENTRAL_MERGE_REVALIDATION_READY":
        return "MERGE_READY", "MERGE_READY", None, False
    if state == "BLOCKED":
        reason = promo["reason"]
        if reason in {"ACTIONABLE_FAILURE", "PLATFORM_GATE"}:
            classification = classify_promotion_failure(evidence)
            failure_class = classification["failure_class"]
            requires_repair = failure_class in {"CODE_FAILURE", "TEST_FAILURE", "QUALITY_FAILURE"}
            target = "REPAIR_REQUIRED" if requires_repair else "BLOCKED"
            return target, target, failure_class, True
        target = _BLOCKED_REASON_ROUTES.get(reason, "BLOCKED")
        return target, target, None, True
    raise PromotionQueueError(f"unexpected promotion evaluation state: {state}")


def advance_promotion_entry(
    queue: dict[str, Any],
    entry_id: str,
    *,
    live: dict[str, Any],
    evidence: dict[str, Any] | None,
    now: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Advance one queued entry using fresh live-branch and CI/review evidence.

    ``live`` feeds reconciliation_promotion.reconcile_packet (branch currency,
    overlap, ownership); ``evidence`` feeds evaluate_promotion (fresh CI, review,
    mergeability, policy) once reconciliation reaches PROMOTION_READY. Passing
    ``evidence=None`` is valid while CI has not reported anything yet.

    This function never grants merge authority; MERGE_READY only means central
    merge authority may revalidate and merge.
    """
    queue = validate_queue(queue)
    entry = queue["entries"].get(entry_id)
    if entry is None:
        raise PromotionQueueError(f"unknown promotion queue entry: {entry_id}")
    if entry["state"] in TERMINAL_STATES:
        return queue, {"status": "NO_OP_TERMINAL", "entry": entry}
    if entry["state"] == "MERGE_ENQUEUED":
        return queue, {"status": "NO_OP_MERGE_IN_FLIGHT", "entry": entry}
    _parse_time(now)

    packet = {
        "packet_id": entry["entry_id"],
        "repository": entry["repository"],
        "branch": entry["branch"],
        "base_sha": entry["base_sha"],
        "exact_head_sha": entry["head_sha"],
        "state": "PREFLIGHT_GREEN",
        "changed_paths": entry["changed_paths"],
    }
    # reconcile_packet()'s "open_pr" blocker exists to defer to a *competing* open
    # PR on this branch. This entry's own tracked PR is not competing with itself.
    live_for_reconciliation = dict(live)
    if entry.get("pr_number") is not None and live_for_reconciliation.get("open_pr") == entry["pr_number"]:
        live_for_reconciliation["open_pr"] = None
    recon = reconcile_packet(packet, live_for_reconciliation)

    if recon["state"] == "BLOCKED":
        updated = _transition(entry, "BLOCKED", now, reason=recon["reason"], failure_class=None, bump_attempt=True)
        return _save(queue, updated), {"status": "BLOCKED", "entry": updated, "reconciliation": recon}

    if recon["state"] == "RECONCILE_REQUIRED":
        if recon["reason"] == "WORKER_HEAD_MOVED":
            new_head = _sha(live.get("branch_head_sha"), "branch_head_sha")
            updated = _rebind_stale_head(entry, new_head, now, reason="LIVE_BRANCH_HEAD_MOVED")
            return _save(queue, updated), {"status": "CI_PENDING", "entry": updated, "reconciliation": recon}
        updated = _transition(entry, "CI_PENDING", now, reason=recon["reason"], failure_class=None, bump_attempt=False)
        return _save(queue, updated), {"status": "CI_PENDING", "entry": updated, "reconciliation": recon}

    # recon["state"] == "PROMOTION_READY"
    if evidence is None:
        updated = _transition(
            entry, "CI_PENDING", now, reason="AWAITING_FRESH_CI_EVIDENCE", failure_class=None, bump_attempt=False
        )
        return _save(queue, updated), {"status": "CI_PENDING", "entry": updated, "reconciliation": recon}

    promo = evaluate_promotion({**packet, "state": "PROMOTION_READY"}, evidence)
    if promo["state"] == "RECONCILE_REQUIRED":
        recovery = promo.get("recovery") or build_stale_promotion_recovery(
            {
                "repository": entry["repository"],
                "branch": entry["branch"],
                "expected_head_sha": entry["head_sha"],
                "current_head_sha": evidence.get("head_sha"),
                "verified_head_sha": evidence.get("verified_head_sha"),
            }
        )
        new_head = _sha(evidence.get("head_sha"), "head_sha")
        updated = _rebind_stale_head(entry, new_head, now, reason="PROMOTION_HEAD_MOVED")
        return _save(queue, updated), {"status": "CI_PENDING", "entry": updated, "promotion": promo, "recovery": recovery}

    status, target_state, failure_class, bump = _map_promotion_result(promo, evidence)
    reset = target_state == "MERGE_READY"
    updated = _transition(
        entry,
        target_state,
        now,
        reason=promo.get("reason", status),
        failure_class=failure_class,
        bump_attempt=bump,
        reset_attempts=reset,
    )
    return _save(queue, updated), {"status": target_state, "entry": updated, "promotion": promo}


def mark_merge_enqueued(queue: dict[str, Any], entry_id: str, *, now: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Record that central merge authority has taken this exact head for merging."""
    queue = validate_queue(queue)
    entry = queue["entries"].get(entry_id)
    if entry is None:
        raise PromotionQueueError(f"unknown promotion queue entry: {entry_id}")
    if entry["state"] != "MERGE_READY":
        raise PromotionQueueError("only a MERGE_READY entry may be enqueued for merge")
    updated = _transition(entry, "MERGE_ENQUEUED", now, reason="CENTRAL_MERGE_DISPATCHED", failure_class=None, bump_attempt=False)
    return _save(queue, updated), {"status": "MERGE_ENQUEUED", "entry": updated}


def mark_merged(
    queue: dict[str, Any],
    entry_id: str,
    *,
    now: str,
    merge_commit_sha: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Record a verified merge. This records evidence only; it grants no merge authority."""
    queue = validate_queue(queue)
    entry = queue["entries"].get(entry_id)
    if entry is None:
        raise PromotionQueueError(f"unknown promotion queue entry: {entry_id}")
    if entry["state"] != "MERGE_ENQUEUED":
        raise PromotionQueueError("only a MERGE_ENQUEUED entry may be marked MERGED")
    updated = _transition(entry, "MERGED", now, reason="MERGE_VERIFIED", failure_class=None, bump_attempt=False, reset_attempts=True)
    if merge_commit_sha is not None:
        updated["merge_commit_sha"] = _sha(merge_commit_sha, "merge_commit_sha")
    return _save(queue, updated), {"status": "MERGED", "entry": updated}


def record_merge_failure(
    queue: dict[str, Any],
    entry_id: str,
    evidence: dict[str, Any],
    *,
    now: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A failed merge attempt returns to the queue with backoff; it is never dropped
    and never left stuck in MERGE_ENQUEUED.
    """
    queue = validate_queue(queue)
    entry = queue["entries"].get(entry_id)
    if entry is None:
        raise PromotionQueueError(f"unknown promotion queue entry: {entry_id}")
    if entry["state"] != "MERGE_ENQUEUED":
        raise PromotionQueueError("only a MERGE_ENQUEUED entry can record a merge failure")
    classification = classify_promotion_failure(evidence)
    failure_class = classification["failure_class"]
    target_state = _MERGE_FAILURE_ROUTES.get(failure_class, "BLOCKED")
    reported_failure_class = None if target_state == "MERGE_READY" else failure_class
    updated = _transition(
        entry,
        target_state,
        now,
        reason=f"MERGE_FAILED:{failure_class}",
        failure_class=reported_failure_class,
        bump_attempt=True,
    )
    return _save(queue, updated), {"status": target_state, "entry": updated, "classification": classification}


def enqueue_and_release_worker(
    queue: dict[str, Any],
    *,
    repository: str,
    branch: str,
    base_sha: str,
    head_sha: str,
    changed_paths: list[str],
    now: str,
    pr_number: int | None = None,
    active_lease: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Publish a verified candidate into the durable queue and release product
    development capacity in the same step.

    From here, PR/CI/review/merge lifecycle belongs to the promotion queue, not
    the worker. This is the only function in this module that touches a lease,
    and it only ever releases one. Nothing below this point (advance, merge,
    merge-failure) acquires or restores a lease, so a promotion failure can
    never re-occupy product development capacity.
    """
    updated_queue, enqueue_result = enqueue_promotion(
        queue,
        repository=repository,
        branch=branch,
        base_sha=base_sha,
        head_sha=head_sha,
        changed_paths=changed_paths,
        now=now,
        pr_number=pr_number,
    )
    released_lease = None
    if active_lease is not None:
        released_lease = release_active_work_lease(active_lease, released_at=now)
    return {
        "queue": updated_queue,
        "enqueue": enqueue_result,
        "released_lease": released_lease,
        "worker_capacity_released": True,
    }


def scan_merge_ready_throughput_defects(
    queue: dict[str, Any],
    *,
    now: str,
    stale_after_seconds: int = DEFAULT_MERGE_READY_STALE_AFTER_SECONDS,
) -> list[dict[str, Any]]:
    """Surface every green, unprocessed MERGE_READY entry as a P1 throughput defect.

    Per docs/EXECUTION_AUTHORITY.md, verified-green merge-ready work left
    unprocessed outranks new work. This is reporting evidence only: it grants
    no merge authority and performs no mutation.
    """
    queue = validate_queue(queue)
    if stale_after_seconds < 0:
        raise PromotionQueueError("stale_after_seconds must be non-negative")
    now_dt = _parse_time(now)
    defects = []
    for entry in queue["entries"].values():
        if entry["state"] != "MERGE_READY":
            continue
        age = (now_dt - _parse_time(entry["updated_at"])).total_seconds()
        if age < 0:
            raise PromotionQueueError("entry updated_at cannot be after now")
        if age >= stale_after_seconds:
            defects.append(
                {
                    "priority": "P1",
                    "defect": "UNPROCESSED_GREEN_MERGE_READY",
                    "entry_id": entry["entry_id"],
                    "repository": entry["repository"],
                    "pr_number": entry.get("pr_number"),
                    "branch": entry["branch"],
                    "head_sha": entry["head_sha"],
                    "age_seconds": age,
                }
            )
    defects.sort(key=lambda item: (-item["age_seconds"], item["entry_id"]))
    return defects
