"""Fail-closed verified-delivery accounting for one supervisor incarnation.

A running process, claimed grant, or new candidate head is NOT a merge.
Only explicit terminal verification quality can count as delivery.
This observer never releases a grant, starts a worker or changes authority.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

_DELIVERED = {"MERGED_VERIFIED", "PRODUCT_REALITY_VERIFIED"}


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _stamp(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


def assess_delivery_throughput(
    state_root: Path,
    authorized_products: list[str],
    pool_snapshot: dict,
    *,
    now: datetime | None = None,
    stall_after_seconds: int = 1800,
) -> dict:
    """Assess current-generation evidence without treating historical grants as progress."""
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("current time must be timezone-aware")
    if stall_after_seconds < 1:
        raise ValueError("stall_after_seconds must be positive")

    identity = _read(state_root / "supervisor-process-instance.json")
    started = _stamp(identity.get("started_at_utc"))
    elapsed = (
        max(0.0, (current - started).total_seconds())
        if started is not None and started <= current
        else None
    )
    raw = _read(state_root / "hands-off-grant-lifecycle.json")
    grants = raw.get("grants", {})
    if not isinstance(grants, dict):
        grants = {}

    candidate_count = 0
    verified_count = 0
    merged_count = 0
    reality_count = 0
    verified_ids = []
    for grant_id, record in grants.items():
        if not isinstance(record, dict) or record.get("state") != "COMPLETED":
            continue
        finished = _stamp(
            record.get("execution_finished_at") or record.get("finished_at")
        )
        if finished is None or started is None or not started <= finished <= current:
            continue
        base = record.get("exact_head")
        head = record.get("head_sha")
        if (
            isinstance(base, str)
            and isinstance(head, str)
            and len(base) == len(head) == 40
            and head != base
        ):
            candidate_count += 1
        quality = record.get("outcome_quality")
        if not isinstance(quality, str) or quality not in _DELIVERED:
            continue
        verified_count += 1
        merged_count += 1
        if quality == "PRODUCT_REALITY_VERIFIED":
            reality_count += 1
        verified_ids.append(str(grant_id))

    executing = pool_snapshot.get("executing_total")
    if type(executing) is not int or executing < 0:
        executing = 0  # malformed telemetry does not grant execution authority
    if not authorized_products:
        status = "NO_AUTHORIZED_WORK"
    elif elapsed is None:
        status = "START_TIME_UNVERIFIED"
    elif verified_count > 0:
        status = "VERIFIED_DELIVERY"
    elif elapsed >= stall_after_seconds:
        status = "VERIFIED_DELIVERY_STALLED"
    else:
        status = "AWAITING_FIRST_VERIFIED_DELIVERY"

    return {
        "schema_version": 1,
        "status": status,
        "verified_deliveries": verified_count,
        "merged_verified": merged_count,
        "product_reality_verified": reality_count,
        "candidate_heads": candidate_count,
        "current_execution_workers": executing,
        "authorized_products": sorted(set(authorized_products)),
        "supervisor_started_at_utc": started.isoformat() if started else None,
        "seconds_since_start": elapsed,
        "stall_after_seconds": stall_after_seconds,
        "verified_grant_ids": verified_ids[-10:],
        "measurement_basis": "current-supervisor-instance-terminal-grant-evidence",
        "execution_authorized": False,
    }
