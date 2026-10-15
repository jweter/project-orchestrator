"""Read-only bridge between durable accepted-event claims and worker lease evidence.

This module is explicitly NOT an acceptance gate, worker launcher, or automatic
recovery engine. Worker SQLite rows have no dispatch claim token; matching a
slice ID/base SHA/time is only correlated evidence, not proven provenance.
No status returned here authorizes a replay, Git ref advance, or next dispatch.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .human_ownership import AUTHORIZED, owner_control_status
from .local_dispatch_event_claim import inspect_claims
from .local_first_state import LocalFirstState
from .local_git_refs import LocalGitRefs

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class OutcomeBridgeError(RuntimeError):
    """The read-only evidence boundary could not be established."""


@dataclass(frozen=True)
class OutcomeBridgeResult:
    event_id: int
    slice_id: str
    disposition: str
    worker_attempt_id: str | None = None
    worker_generation: int | None = None
    candidate_sha: str | None = None
    receipt_sha256: str | None = None


def _owner(root: Path) -> None:
    if owner_control_status(root) != AUTHORIZED:
        raise OutcomeBridgeError("OWNER_CONTROL_VETO")


def _readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file() or path.is_symlink():
        raise OutcomeBridgeError("WORKER_STORE_UNAVAILABLE")
    try:
        db = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        return db
    except sqlite3.Error as exc:
        raise OutcomeBridgeError("WORKER_STORE_UNREADABLE") from exc


def _epoch(value: object) -> float:
    if not isinstance(value, str):
        raise ValueError("INVALID_TIMESTAMP")
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("NAIVE_TIMESTAMP")
    return dt.timestamp()


def _same_root(first: object, second: Path) -> bool:
    return isinstance(first, str) and Path(first).resolve() == Path(second).resolve()


def _result(row: dict[str, Any], status: str, **kwargs: Any) -> OutcomeBridgeResult:
    return OutcomeBridgeResult(
        event_id=row["acceptance_event_id"],
        slice_id=row["slice_id"],
        disposition=status,
        **kwargs,
    )


def reconcile_dispatch_outcomes(
    *,
    state: LocalFirstState,
    refs: LocalGitRefs,
    owner_state_root: Path,
    claims_path: Path,
    worker_db_path: Path,
) -> list[OutcomeBridgeResult]:
    """Classify all claims without repairing, writing, scheduling, or inferring success.

    The authoritative source remains accepted Git refs and SQLite milestones.
    This never treats a worker-reported VERIFIED_CANDIDATE as independent proof.
    Dispatch-to-lease provenance must be added before any automatic advancement.
    """
    _owner(owner_state_root)
    if refs.owner_state_root.resolve() != owner_state_root.resolve():
        raise OutcomeBridgeError("OWNER_ROOT_MISMATCH")
    if not claims_path.is_file():
        return []
    claims = inspect_claims(claims_path)
    if not claims:
        return []

    snapshot = state.snapshot()
    milestones = {r["milestone_id"]: r for r in snapshot["milestones"]}
    intents = {r["milestone_id"]: r for r in snapshot["acceptance_intents"]}
    journal = {
        (r["event_id"], r["milestone_id"], r["detail"])
        for r in snapshot["journal"]
        if r["event_type"] == "MILESTONE_ACCEPTED"
    }
    source_id = os.path.normcase(str(state.path.resolve()))
    work_db: sqlite3.Connection | None = None
    try:
        if worker_db_path.is_file():
            work_db = _readonly(worker_db_path)
            version = work_db.execute("PRAGMA user_version").fetchone()[0]
            if version != 4:
                raise OutcomeBridgeError("WORKER_SCHEMA_UNSUPPORTED")
        outcomes: list[OutcomeBridgeResult] = []
        for row in claims:
            _owner(owner_state_root)
            milestone_id = row["milestone_id"]
            accepted_sha = row["accepted_sha"]
            intent = intents.get(milestone_id)
            milestone = milestones.get(milestone_id)
            if (
                row["source_db"] != source_id
                or not _same_root(row["owner_root"], owner_state_root)
                or not intent
                or intent["state"] != "COMMITTED"
                or not milestone
                or intent["accepted_sha"] != accepted_sha
                or milestone["accepted_sha"] != accepted_sha
                or row["repository"] != intent["repository"]
                or milestone["repository"] != intent["repository"]
                or milestone["verification_sha256"] != intent["verification_sha256"]
                or (row["acceptance_event_id"], milestone_id, accepted_sha)
                not in journal
            ):
                outcomes.append(_result(row, "CLAIM_SOURCE_PROVENANCE_CONFLICT"))
                continue
            if (
                refs.accepted() != accepted_sha
                or refs.get("milestone", milestone_id) != accepted_sha
            ):
                outcomes.append(_result(row, "ACCEPTED_REF_CHANGED_HOLD"))
                continue
            try:
                admitted = _epoch(row["claimed_utc"])
            except (ValueError, OverflowError):
                outcomes.append(_result(row, "CLAIM_TIMESTAMP_UNVERIFIED"))
                continue
            if work_db is None:
                outcomes.append(_result(row, "WORKER_STORE_MISSING"))
                continue
            slice_id = row["slice_id"]
            leases = work_db.execute(
                "SELECT * FROM slice_lease_events "
                "WHERE slice_id=? AND event_type='SLICE_LEASED' ORDER BY seq",
                (slice_id,),
            ).fetchall()
            if row["state"] == "CLAIMED":
                status = (
                    "CLAIMED_WITH_UNEXPECTED_WORKER_LEASE_REVIEW"
                    if leases
                    else "CLAIMED_NOT_INVOKED"
                )
                outcomes.append(_result(row, status))
                continue
            if row["state"] not in {"INVOCATION_RESERVED", "RESULT_RECORDED"}:
                outcomes.append(_result(row, "INVALID_DISPATCH_STATE"))
                continue
            if not leases:
                status = (
                    "RETURNED_WITHOUT_WORKER_LEASE_REVIEW"
                    if row["state"] == "RESULT_RECORDED"
                    else "INVOCATION_UNCERTAIN_NO_LEASE"
                )
                outcomes.append(_result(row, status))
                continue
            if len(leases) != 1:
                outcomes.append(_result(row, "MULTIPLE_WORKER_ATTEMPTS_REVIEW"))
                continue
            lease_event = leases[0]
            if (
                lease_event["detail"] != accepted_sha
                or not isinstance(lease_event["occurred_at"], (float, int))
                or lease_event["occurred_at"] < admitted
            ):
                outcomes.append(_result(row, "WORKER_LEASE_BASE_OR_TIME_CONFLICT"))
                continue
            attempt = lease_event["attempt_id"]
            generation = lease_event["generation"]
            kwargs = {"worker_attempt_id": attempt, "worker_generation": generation}
            active = work_db.execute(
                "SELECT * FROM slice_leases WHERE slice_id=?", (slice_id,)
            ).fetchone()
            if active is not None and (
                active["attempt_id"] != attempt
                or active["generation"] != generation
                or active["base_sha"] != accepted_sha
            ):
                outcomes.append(_result(row, "WORKER_ACTIVE_LEASE_CONFLICT"))
                continue
            reported = work_db.execute(
                "SELECT * FROM slice_outcomes WHERE slice_id=?", (slice_id,)
            ).fetchone()
            if reported is None:
                outcomes.append(
                    _result(row, "WORKER_OUTCOME_UNKNOWN_NO_AUTOREPLAY", **kwargs)
                )
                continue
            if (
                reported["attempt_id"] != attempt
                or reported["generation"] != generation
                or reported["base_sha"] != accepted_sha
            ):
                outcomes.append(_result(row, "WORKER_OUTCOME_IDENTITY_CONFLICT"))
                continue
            if reported["state"] == "FAILED_ROUTED":
                outcomes.append(_result(row, "WORKER_FAILURE_REPAIR_REVIEW", **kwargs))
                continue
            if reported["state"] != "VERIFIED_CANDIDATE":
                outcomes.append(_result(row, "WORKER_OUTCOME_STATE_UNVERIFIED"))
                continue
            try:
                evidence = json.loads(reported["evidence_json"])
            except (TypeError, json.JSONDecodeError):
                evidence = None
            candidate_sha = reported["candidate_sha"]
            digest = (
                evidence.get("receipt_sha256") if isinstance(evidence, dict) else None
            )
            if (
                not isinstance(candidate_sha, str)
                or not _SHA.fullmatch(candidate_sha)
                or candidate_sha == accepted_sha
                or not isinstance(digest, str)
                or not _DIGEST.fullmatch(digest)
            ):
                outcomes.append(_result(row, "WORKER_EVIDENCE_UNVERIFIED"))
                continue
            # Even a structurally valid worker outcome is NEVER sufficient for
            # acceptance: no claim token accompanies this worker's lease record.
            outcomes.append(
                _result(
                    row,
                    "WORKER_OUTCOME_UNBOUND_NEEDS_INDEPENDENT_GIT_PROOF",
                    candidate_sha=candidate_sha,
                    receipt_sha256=digest,
                    **kwargs,
                )
            )
        _owner(owner_state_root)
        return outcomes
    finally:
        if work_db is not None:
            work_db.close()
