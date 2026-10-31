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


def recent_requests(
    db: sqlite3.Connection, *, limit: int = 20
) -> tuple[DesktopRequest, ...]:
    """Return a bounded audit view, most recent first, without DB mutation."""
    _connection(db)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer between 1 and 100")
    rows = db.execute(
        f"SELECT {_COLUMNS} FROM iron_god_desktop_requests ORDER BY rowid DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return tuple(_record(row) for row in rows)


def latest_matching_action(
    db: sqlite3.Connection, action: str
) -> DesktopRequest | None:
    """Look up most recent action for diagnostics; not dispatch eligibility."""
    _connection(db)
    if action not in ("GIVE_ME_CONTROL", "TAKE_THE_WHEEL"):
        raise ValueError("unrecognized desktop action")
    row = db.execute(
        f"SELECT {_COLUMNS} FROM iron_god_desktop_requests "
        "WHERE action=? ORDER BY rowid DESC LIMIT 1",
        (action,),
    ).fetchone()
    return _record(row) if row is not None else None


@dataclass(frozen=True)
class QueueDepth:
    pending: int
    observed: int
    total: int
    execution_authorized: bool = False


def queue_depth(db: sqlite3.Connection) -> QueueDepth:
    """Count all recorded requests without treating old requests as eligible."""
    _connection(db)
    rows = db.execute(
        "SELECT state, COUNT(*) FROM iron_god_desktop_requests GROUP BY state"
    ).fetchall()
    counts = dict(rows)
    return QueueDepth(
        pending=counts.get("REQUESTED_NOT_AUTHORIZED", 0),
        observed=counts.get("OBSERVED_NOT_AUTHORIZED", 0),
        total=sum(counts.values()),
    )


@dataclass(frozen=True)
class TableReadiness:
    status: str
    missing_columns: tuple[str, ...] = ()
    execution_authorized: bool = False


_REQUIRED_COLUMNS = frozenset(
    (
        "command_id",
        "action",
        "requested_at_utc",
        "state",
        "observed_at_utc",
        "observer_generation",
        "observer_release_sha",
    )
)


def request_table_readiness(db: sqlite3.Connection) -> TableReadiness:
    """Read-only schema inspection; existence alone is not a valid contract."""
    _connection(db)
    row = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='iron_god_desktop_requests'"
    ).fetchone()
    if row is None:
        return TableReadiness("TABLE_MISSING")
    ddl = row[0]
    if not isinstance(ddl, str):
        return TableReadiness("TABLE_DEFINITION_UNREADABLE")
    cols = {
        row[1]
        for row in db.execute(
            "PRAGMA table_info('iron_god_desktop_requests')"
        ).fetchall()
    }
    missing = tuple(sorted(_REQUIRED_COLUMNS.difference(cols)))
    if missing:
        return TableReadiness("REQUIRED_COLUMNS_MISSING", missing)
    if "REQUESTED_NOT_AUTHORIZED" not in ddl or "OBSERVED_NOT_AUTHORIZED" not in ddl:
        return TableReadiness("NON_AUTHORIZING_STATES_NOT_ENFORCED")
    if "CHECK" not in ddl.upper():
        return TableReadiness("STATE_CONSTRAINTS_MISSING")
    return TableReadiness("STAGED_SCHEMA_PRESENT_NOT_AUTHORIZED")


def inspect_queue(
    db: sqlite3.Connection,
    *,
    now: datetime | None = None,
    max_age_seconds: int = 120,
    pressure_threshold: int = 500,
) -> tuple[InboxDecision, QueueDepth, TableReadiness, str]:
    """Synthesize inbox + backlog + schema without enabling dispatch."""
    if type(pressure_threshold) is not int or pressure_threshold < 1:
        raise ValueError("pressure threshold must be positive")
    schema = request_table_readiness(db)
    if schema.status != "STAGED_SCHEMA_PRESENT_NOT_AUTHORIZED":
        return (
            InboxDecision("SCHEMA_UNVERIFIED", None),
            QueueDepth(0, 0, 0),
            schema,
            "BLOCKED",
        )
    decision = inspect_inbox(db, now=now, max_age_seconds=max_age_seconds)
    counts = queue_depth(db)
    status = (
        "BACKLOG_WARNING" if counts.pending > pressure_threshold else "NOT_AUTHORIZED"
    )
    return decision, counts, schema, status


def main(argv: list[str] | None = None) -> int:
    """Read-only live inbox status CLI. Never implies command completion."""
    import argparse
    import json
    from dataclasses import asdict
    from pathlib import Path

    parser = argparse.ArgumentParser(
        description="Read-only Iron God desktop inbox diagnostics (no authorization)."
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args(argv)

    if not args.database.is_file() or args.database.is_symlink():
        print(json.dumps({"status": "EXISTING_DATABASE_REQUIRED"}))
        return 3
    try:
        db = sqlite3.connect(
            args.database.resolve().as_uri() + "?mode=ro",
            uri=True,
            isolation_level=None,
            timeout=5,
        )
        try:
            decision, counts, schema, status = inspect_queue(db)
            output = {
                "status": status if status != "NOT_AUTHORIZED" else decision.status,
                "inbox_status": decision.status,
                "schema": schema.status,
                "queue": {
                    "pending": counts.pending,
                    "observed": counts.observed,
                    "total": counts.total,
                },
                "recent": [
                    {
                        "command_id": x.command_id,
                        "action": x.action,
                        "state": x.state,
                        "requested_at_utc": x.requested_at_utc,
                    }
                    for x in (
                        recent_requests(db, limit=args.limit)
                        if schema.status == "STAGED_SCHEMA_PRESENT_NOT_AUTHORIZED"
                        else ()
                    )
                ],
                "execution_authorized": False,
                "acknowledged_by_engine": False,
            }
            print(json.dumps(output, sort_keys=True))
            return 5 if schema.status == "STAGED_SCHEMA_PRESENT_NOT_AUTHORIZED" else 3
        finally:
            db.close()
    except (sqlite3.Error, ValueError, OSError) as exc:
        print(
            json.dumps(
                {
                    "status": "INBOX_UNVERIFIED",
                    "reason": type(exc).__name__,
                    "execution_authorized": False,
                }
            )
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
