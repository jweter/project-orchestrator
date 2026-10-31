"""Classify quarantined development grants without modifying grant authority.

Historical quarantine is not the same thing as a current live-worker failure.
The summary gives operators exact reasons and counts without exposing tokens
or assuming that an unknown worker identity is dead.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime


def triage_quarantines(
    quarantines: dict[str, dict],
    *,
    now: datetime | None = None,
    recent_seconds: int = 86400,
) -> dict:
    if recent_seconds <= 0:
        raise ValueError("recent_seconds must be positive")
    current = now or datetime.now(UTC)
    by_reason: Counter[str] = Counter()
    by_recovery: Counter[str] = Counter()
    recent = 0
    old = 0
    timestamp_unknown = 0
    owner_identity_recorded = 0
    preserved_work = 0
    for record in quarantines.values():
        if not isinstance(record, dict):
            continue
        reason = record.get("result_status")
        recovery = record.get("recovery_result")
        by_reason[str(reason or "UNKNOWN_REASON")] += 1
        by_recovery[str(recovery or "UNKNOWN_RECOVERY_STATE")] += 1
        if isinstance(recovery, str) and "PRESERVED" in recovery:
            preserved_work += 1
        if record.get("worker_pid") and record.get("process_start_fingerprint"):
            owner_identity_recorded += 1
        raw = record.get("finished_at")
        try:
            date = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if date.tzinfo is None:
                raise ValueError("timezone missing")
        except (TypeError, ValueError):
            timestamp_unknown += 1
            continue
        age = (current - date).total_seconds()
        if age < 0:  # a future timestamp is skewed or corrupt evidence, not proof of an old grant
            timestamp_unknown += 1
        elif age <= recent_seconds:
            recent += 1
        else:
            old += 1
    count = sum(by_reason.values())
    return {
        "schema_version": 1,
        "count": count,
        "recent_count": recent,
        "historical_count": old,
        "age_unverified_count": timestamp_unknown,
        "owner_identity_recorded_count": owner_identity_recorded,
        "preserved_work_count": preserved_work,
        "by_reason": dict(sorted(by_reason.items())),
        "by_recovery_result": dict(sorted(by_recovery.items())),
        "disposition": "REVIEW_INDIVIDUALLY_DO_NOT_BULK_RELEASE"
        if count
        else "NO_QUARANTINES",
        "authority_mutated": False,
    }
