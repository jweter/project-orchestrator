"""Read-only supervisor-side inbox for staged Iron God desktop requests.

No owner-control authority, execution grants, process lifecycle management, or
command completion acknowledgements originate here. The inbox only inspects
the existing operational SQLite store.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from .iron_god_desktop_requests import (
    DesktopRequest,
    DesktopRequestError,
    _connection,
    _parse_stamp,
    _record,
)


@dataclass(frozen=True)
class InboxDecision:
    status: str
    request: DesktopRequest | None
    execution_authorized: bool = False


_COLUMNS = (
    "command_id, action, requested_at_utc, state, observed_at_utc, "
    "observer_generation, observer_release_sha"
)


def latest_request(db: sqlite3.Connection) -> DesktopRequest | None:
    """Read the most recent owner's command, including observed commands."""
    _connection(db)
    row = db.execute(
        f"SELECT {_COLUMNS} FROM iron_god_desktop_requests ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    return _record(row) if row is not None else None


def latest_unobserved(db: sqlite3.Connection) -> DesktopRequest | None:
    """Only the newest command can be queued; older ones are superseded."""
    item = latest_request(db)
    if item is None or item.state != "REQUESTED_NOT_AUTHORIZED":
        return None
    return item


def inspect_inbox(
    db: sqlite3.Connection,
    *,
    now: datetime | None = None,
    max_age_seconds: int = 120,
) -> InboxDecision:
    """Classify the newest request; never call the observation writer.

    The caller must independently verify owner authority and any live process
    state before doing more than read from the inbox.
    """
    if (
        not isinstance(max_age_seconds, int)
        or isinstance(max_age_seconds, bool)
        or max_age_seconds <= 0
    ):
        raise ValueError("positive integer horizon required")
    clock = now if now is not None else datetime.now(UTC)
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("aware clock required")
    clock = clock.astimezone(UTC)
    item = latest_request(db)
    if item is None:
        return InboxDecision("NO_REQUEST", None)
    if item.state == "OBSERVED_NOT_AUTHORIZED":
        return InboxDecision("ALREADY_OBSERVED_NOT_AUTHORIZED", item)
    if item.state != "REQUESTED_NOT_AUTHORIZED":
        return InboxDecision("INVALID_REQUEST_STATE", item)
    try:
        stamp = _parse_stamp(item.requested_at_utc)
    except DesktopRequestError:
        return InboxDecision("INVALID_REQUEST_TIMESTAMP", item)
    age = (clock - stamp).total_seconds()
    if age < 0:
        return InboxDecision("FUTURE_REQUEST", item)
    if age > max_age_seconds:
        return InboxDecision("EXPIRED_REQUEST", item)
    return InboxDecision("PENDING_UNAUTHORIZED", item)
