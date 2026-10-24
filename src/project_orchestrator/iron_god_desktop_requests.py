"""Durable, non-authorizing owner desktop command *requests*.

This module is a small SQLite extension for the existing Iron God operational
database. It is not an owner-control authority, agent launcher, or completion
acknowledgment. Neither writing nor observing a request may change controller
mode, authorize a candidate, or claim that a desktop command succeeded.

The caller supplies an already-open SQLite connection to the existing
operational database; this module never selects a file path, creates another
database, inspects the retired owner marker, or changes PRAGMA user_version.
No live code invokes it until an independently approved controller cutover.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

_ACTIONS = frozenset({"GIVE_ME_CONTROL", "TAKE_THE_WHEEL"})
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_STATES = frozenset({"REQUESTED_NOT_AUTHORIZED", "OBSERVED_NOT_AUTHORIZED"})


class DesktopRequestError(RuntimeError):
    """A malformed, duplicate, stale, or unfenced control request."""


@dataclass(frozen=True)
class DesktopRequest:
    command_id: str
    action: str
    requested_at_utc: str
    state: str
    observed_at_utc: str | None = None
    observer_generation: str | None = None
    observer_release_sha: str | None = None
    execution_authorized: bool = False


def _clock(now: datetime | None) -> datetime:
    clock = now if now is not None else datetime.now(UTC)
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("A timezone-aware clock is required")
    return clock.astimezone(UTC)


def _parse_stamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise DesktopRequestError("STORED_TIMESTAMP_INVALID") from exc
    if parsed.tzinfo is None:
        raise DesktopRequestError("STORED_TIMESTAMP_NOT_AWARE")
    return parsed.astimezone(UTC)


def _connection(db: sqlite3.Connection) -> sqlite3.Connection:
    if not isinstance(db, sqlite3.Connection):
        raise TypeError("An existing SQLite connection is required")
    if db.in_transaction:
        raise DesktopRequestError("CALLER_TRANSACTION_ACTIVE")
    return db


def install_request_table(db: sqlite3.Connection) -> None:
    """Add one table to the supplied existing database; never migrate others."""
    _connection(db)
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS iron_god_desktop_requests (
            command_id TEXT PRIMARY KEY NOT NULL,
            action TEXT NOT NULL
                CHECK (action IN ('GIVE_ME_CONTROL', 'TAKE_THE_WHEEL')),
            requested_at_utc TEXT NOT NULL,
            state TEXT NOT NULL
                CHECK (state IN ('REQUESTED_NOT_AUTHORIZED',
                                 'OBSERVED_NOT_AUTHORIZED')),
            observed_at_utc TEXT,
            observer_generation TEXT,
            observer_release_sha TEXT,
            CHECK (
                (state = 'REQUESTED_NOT_AUTHORIZED'
                    AND observed_at_utc IS NULL AND observer_generation IS NULL
                    AND observer_release_sha IS NULL)
                OR
                (state = 'OBSERVED_NOT_AUTHORIZED'
                    AND observed_at_utc IS NOT NULL
                    AND observer_generation IS NOT NULL
                    AND observer_release_sha IS NOT NULL)
            )
        )
        """
    )


def _record(row: sqlite3.Row | tuple) -> DesktopRequest:
    if isinstance(row, sqlite3.Row):
        values = {
            key: row[key]
            for key in (
                "command_id",
                "action",
                "requested_at_utc",
                "state",
                "observed_at_utc",
                "observer_generation",
                "observer_release_sha",
            )
        }
    else:
        values = dict(
            zip(
                (
                    "command_id",
                    "action",
                    "requested_at_utc",
                    "state",
                    "observed_at_utc",
                    "observer_generation",
                    "observer_release_sha",
                ),
                row,
                strict=True,
            )
        )
    return DesktopRequest(**values)


def get_request(db: sqlite3.Connection, command_id: str) -> DesktopRequest | None:
    """Read an existing request. No schema initialization or state mutation."""
    _connection(db)
    row = db.execute(
        "SELECT command_id, action, requested_at_utc, state, observed_at_utc, "
        "observer_generation, observer_release_sha "
        "FROM iron_god_desktop_requests WHERE command_id=?",
        (command_id,),
    ).fetchone()
    return _record(row) if row is not None else None


def submit_request(
    db: sqlite3.Connection,
    *,
    action: str,
    command_id: str | None = None,
    now: datetime | None = None,
) -> DesktopRequest:
    """Record intent, never owner release or owner takeover success.

    Both requests remain inert until a separately authorized engine consumes
    them. The random command ID must be echoed in every later observation.
    """
    _connection(db)
    if action not in _ACTIONS:
        raise DesktopRequestError("ACTION_UNSUPPORTED")
    ident = command_id if command_id is not None else uuid4().hex
    if not isinstance(ident, str) or re.fullmatch(r"[0-9a-f]{32}", ident) is None:
        raise DesktopRequestError("COMMAND_ID_INVALID")
    stamp = _clock(now).isoformat()
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute(
            "INSERT INTO iron_god_desktop_requests "
            "(command_id, action, requested_at_utc, state) VALUES (?,?,?,?)",
            (ident, action, stamp, "REQUESTED_NOT_AUTHORIZED"),
        )
        db.execute("COMMIT")
    except sqlite3.IntegrityError as exc:
        db.execute("ROLLBACK")
        raise DesktopRequestError("COMMAND_ALREADY_EXISTS") from exc
    except BaseException:
        db.execute("ROLLBACK")
        raise
    return DesktopRequest(
        command_id=ident,
        action=action,
        requested_at_utc=stamp,
        state="REQUESTED_NOT_AUTHORIZED",
    )


def record_observation(
    db: sqlite3.Connection,
    *,
    command_id: str,
    observer_generation: str,
    observer_release_sha: str,
    now: datetime | None = None,
    max_age_seconds: int = 120,
) -> DesktopRequest:
    """Durably observe a request, explicitly *not* acknowledge completion.

    Observation records supervisor identity for later reconciliation only.
    It cannot transition into a success/authorization state, toggle a control
    flag, lease a worker, or create a fabricated PAUSED/APPLIED receipt.
    """
    _connection(db)
    moment = _clock(now)
    if not isinstance(max_age_seconds, int) or max_age_seconds <= 0:
        raise ValueError("A positive request freshness horizon is required")
    if (
        not isinstance(observer_generation, str)
        or not observer_generation
        or len(observer_generation) > 128
        or not isinstance(observer_release_sha, str)
        or _SHA.fullmatch(observer_release_sha) is None
    ):
        raise DesktopRequestError("OBSERVER_IDENTITY_UNVERIFIED")
    db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute(
            "SELECT command_id, action, requested_at_utc, state, "
            "observed_at_utc, observer_generation, observer_release_sha "
            "FROM iron_god_desktop_requests WHERE command_id=?",
            (command_id,),
        ).fetchone()
        if row is None:
            raise DesktopRequestError("COMMAND_NOT_FOUND")
        previous = _record(row)
        # Order is part of the safety boundary: a newer owner click
        # supersedes earlier unexecuted requests. In particular, a pending
        # Take the Wheel request cannot be observed after Give Me Control.
        newest = db.execute(
            "SELECT command_id FROM iron_god_desktop_requests "
            "ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if newest is None or newest[0] != command_id:
            raise DesktopRequestError("COMMAND_SUPERSEDED_BY_NEWER_REQUEST")
        if previous.state not in _STATES:
            raise DesktopRequestError("COMMAND_STATE_INVALID")
        elapsed = (moment - _parse_stamp(previous.requested_at_utc)).total_seconds()
        if elapsed < 0 or elapsed > max_age_seconds:
            raise DesktopRequestError("COMMAND_NOT_FRESH")
        if previous.state == "OBSERVED_NOT_AUTHORIZED":
            if (
                previous.observer_generation != observer_generation
                or previous.observer_release_sha != observer_release_sha
            ):
                raise DesktopRequestError("OBSERVATION_IDENTITY_CONFLICT")
            db.execute("COMMIT")
            return previous
        observed_stamp = moment.isoformat()
        changed = db.execute(
            "UPDATE iron_god_desktop_requests "
            "SET state=?, observed_at_utc=?, observer_generation=?, "
            "observer_release_sha=? "
            "WHERE command_id=? AND state='REQUESTED_NOT_AUTHORIZED'",
            (
                "OBSERVED_NOT_AUTHORIZED",
                observed_stamp,
                observer_generation,
                observer_release_sha,
                command_id,
            ),
        ).rowcount
        if changed != 1:
            raise DesktopRequestError("OBSERVATION_STATE_CHANGED")
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    return DesktopRequest(
        command_id=previous.command_id,
        action=previous.action,
        requested_at_utc=previous.requested_at_utc,
        state="OBSERVED_NOT_AUTHORIZED",
        observed_at_utc=observed_stamp,
        observer_generation=observer_generation,
        observer_release_sha=observer_release_sha,
    )


def main(argv: list[str] | None = None) -> int:
    """Staged CLI transport; exits nonzero for requests lacking engine proof.

    This never claims command success. An icon must not treat a queued request
    as an acknowledged pause/resume, even if SQLite committed successfully.
    """
    import argparse
    import json
    from dataclasses import asdict
    from pathlib import Path

    parser = argparse.ArgumentParser(
        description="Stage inert Iron God desktop requests in an EXISTING SQLite store."
    )
    parser.add_argument("--database", required=True, type=Path)
    commands = parser.add_mutually_exclusive_group(required=True)
    commands.add_argument("--prepare", action="store_true")
    commands.add_argument("--action", choices=sorted(_ACTIONS))
    commands.add_argument("--inspect", metavar="COMMAND_ID")
    args = parser.parse_args(argv)

    path = args.database
    if not path.is_file() or path.is_symlink():
        print(json.dumps({"status": "EXISTING_DATABASE_REQUIRED"}))
        return 3

    try:
        db = sqlite3.connect(
            path.resolve().as_uri() + "?mode=rw",
            uri=True,
            isolation_level=None,
            timeout=10,
        )
        try:
            if args.prepare:
                present = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='acceptance_dispatch_claims'"
                ).fetchone()
                if present is None:
                    raise DesktopRequestError("EXISTING_IRON_GOD_CLAIMS_STORE_REQUIRED")
                install_request_table(db)
                print(json.dumps({"status": "SCHEMA_READY_NOT_AUTHORIZED"}))
                return 0
            if args.action:
                item = submit_request(db, action=args.action)
            else:
                item = get_request(db, args.inspect)
                if item is None:
                    raise DesktopRequestError("COMMAND_NOT_FOUND")
            print(json.dumps(asdict(item), sort_keys=True))
            # No existing VBS success notification may mistake a request
            # recorded in SQLite for a completed control transition.
            return 5
        finally:
            db.close()
    except (sqlite3.Error, DesktopRequestError, OSError) as exc:
        print(
            json.dumps(
                {
                    "status": "REQUEST_TRANSPORT_UNVERIFIED",
                    "reason": type(exc).__name__,
                }
            )
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
