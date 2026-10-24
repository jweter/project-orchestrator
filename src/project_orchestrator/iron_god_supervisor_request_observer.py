"""Opt-in, non-authorizing supervisor-side observation of desktop requests.

Consumes a durable request from the existing Iron God SQLite claims store and
records only OBSERVED_NOT_AUTHORIZED. No owner-control state is read or
written here. This does not PAUSE/RESUME workers or acknowledge completion.

Live launcher opt-in is a separate, gated production cutover.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from .iron_god_desktop_requests import (
    DesktopRequestError,
    get_request,
    record_observation,
)


@dataclass(frozen=True)
class SupervisorRequestObservation:
    status: str
    command_id: str | None = None
    action: str | None = None
    observer_generation: str | None = None
    execution_authorized: bool = False


def observe_next_desktop_request(
    *,
    claims_db: Path | None,
    supervisor_generation: str,
    release_sha: str | None,
    now: datetime | None = None,
    max_age_seconds: int = 120,
) -> SupervisorRequestObservation:
    """Observe newest eligible request; never grant or pretend to complete it.

    All inputs must be supplied by the already-running, fenced supervisor.
    A missing DB/table/release proof returns a diagnostic, not an installation
    attempt. No database is created. The real owner veto and dispatch gates
    remain upstream and unchanged.
    """
    if claims_db is None:
        return SupervisorRequestObservation(status="NOT_CONFIGURED")
    if not isinstance(supervisor_generation, str) or not re.fullmatch(
        r"[0-9a-f]{32}", supervisor_generation
    ):
        return SupervisorRequestObservation(status="SUPERVISOR_GENERATION_UNVERIFIED")
    if not isinstance(release_sha, str) or not re.fullmatch(
        r"[0-9a-f]{40}", release_sha
    ):
        return SupervisorRequestObservation(status="RELEASE_IDENTITY_UNVERIFIED")

    path = Path(claims_db)
    try:
        if path.is_symlink() or not path.is_file():
            return SupervisorRequestObservation(status="CLAIMS_STORE_UNAVAILABLE")
        uri = path.resolve().as_uri() + "?mode=rw"
        db = sqlite3.connect(uri, uri=True, timeout=3, isolation_level=None)
    except (OSError, sqlite3.Error, ValueError):
        return SupervisorRequestObservation(status="CLAIMS_STORE_UNAVAILABLE")

    try:
        # Fail closed unless this is the existing Iron God claims database and
        # an installed, opt-in request table is present. Never call installer.
        required = {"acceptance_dispatch_claims", "iron_god_desktop_requests"}
        present = {
            name
            for (name,) in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name IN ('acceptance_dispatch_claims',"
                "'iron_god_desktop_requests')"
            ).fetchall()
        }
        if present != required:
            return SupervisorRequestObservation(
                status="REQUEST_TRANSPORT_NOT_INSTALLED"
            )
        row = db.execute(
            "SELECT command_id FROM iron_god_desktop_requests "
            "ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return SupervisorRequestObservation(status="NO_REQUEST")
        ticket = get_request(db, row[0])
        if ticket is None:
            return SupervisorRequestObservation(status="REQUEST_DISAPPEARED")
        if ticket.state != "REQUESTED_NOT_AUTHORIZED":
            return SupervisorRequestObservation(
                status="ALREADY_OBSERVED_NOT_AUTHORIZED",
                command_id=ticket.command_id,
                action=ticket.action,
                observer_generation=ticket.observer_generation,
            )
        try:
            result = record_observation(
                db,
                command_id=ticket.command_id,
                observer_generation=supervisor_generation,
                observer_release_sha=release_sha,
                now=now,
                max_age_seconds=max_age_seconds,
            )
        except DesktopRequestError as exc:
            return SupervisorRequestObservation(
                status=str(exc),
                command_id=ticket.command_id,
                action=ticket.action,
            )
        return SupervisorRequestObservation(
            status=result.state,
            command_id=result.command_id,
            action=result.action,
            observer_generation=result.observer_generation,
        )
    except sqlite3.Error:
        return SupervisorRequestObservation(status="CLAIMS_STORE_QUERY_FAILED")
    finally:
        db.close()


def supervisor_observation_snapshot(
    claims_db: Path | None, supervisor_generation: str, release_sha: str | None
) -> dict:
    """Presentation-only projection for the existing supervisor snapshot."""
    return asdict(
        observe_next_desktop_request(
            claims_db=claims_db,
            supervisor_generation=supervisor_generation,
            release_sha=release_sha,
        )
    )
