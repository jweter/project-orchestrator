"""Read-only commissioning diagnostics (2026-10-18, owner directive:
"Improving error reporting so future commissioning failures identify
the exact rejected authorization predicate" and "Completing read-only
diagnostics that distinguish a claimed task, reserved invocation,
leased worker, and executed builder").

Neither function here dispatches, claims, leases, builds, or writes
anything. Both compose the real, already-existing primitives
(inspect_claims; the real controller state file; the real gate()
function from the loaded local_continuous_worker.py module) rather
than re-deriving their logic independently -- re-deriving logic that
already exists elsewhere is exactly the kind of drift this session
already found and fixed once (the task-construction duplication bug),
so this module never reimplements gate()'s own file-reading logic; it
calls the real function, supplied by the caller.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .local_dispatch_event_claim import inspect_claims


@dataclass(frozen=True)
class AuthorizationDiagnosis:
    overall_authorized: bool
    blocking_predicates: tuple[str, ...]
    mode: str | None
    candidate_execution_authorized: object
    gate_result: bool | None
    controller_state_readable: bool


def diagnose_candidate_authorization(
    *, controller_state_path: Path, gate_fn: Callable[[], bool]
) -> AuthorizationDiagnosis:
    """Report EXACTLY which predicate
    execute_candidate_attempt's own default authorized_fn would
    evaluate, and why it passes or fails -- without ever calling
    execute_candidate_attempt, dispatching anything, or writing
    anything. Safe to call before, during, or after any real attempt.

    `gate_fn` must be the real gate() function from the loaded
    local_continuous_worker.py module (never a reimplementation) --
    this is the caller's responsibility, enforced by convention, not
    by this function importing that module itself (which would create
    an import-time coupling this module does not need for its other
    use, pure read-only state-file inspection).
    """
    try:
        state = json.loads(controller_state_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return AuthorizationDiagnosis(
            overall_authorized=False,
            blocking_predicates=(f"CONTROLLER_STATE_UNREADABLE:{exc}",),
            mode=None,
            candidate_execution_authorized=None,
            gate_result=None,
            controller_state_readable=False,
        )
    mode = state.get("mode")
    candidate_execution_authorized = state.get("candidate_execution_authorized")
    gate_result = gate_fn()

    mode_ok = mode == "candidate_mode"
    authorized_ok = candidate_execution_authorized is True

    blocking: list[str] = []
    if not mode_ok:
        blocking.append(f"mode is {mode!r}, not 'candidate_mode'")
    if not authorized_ok:
        blocking.append(
            f"candidate_execution_authorized is {candidate_execution_authorized!r}, not True"
        )
    if not gate_result:
        blocking.append("gate() returned False (owner control active or unverified)")

    return AuthorizationDiagnosis(
        overall_authorized=mode_ok and authorized_ok and gate_result,
        blocking_predicates=tuple(blocking),
        mode=mode,
        candidate_execution_authorized=candidate_execution_authorized,
        gate_result=gate_result,
        controller_state_readable=True,
    )


@dataclass(frozen=True)
class ClaimLifecycleDiagnosis:
    slice_id: str
    claim_exists: bool
    claim_state: str | None
    claim_token: str | None
    acceptance_event_id: int | None
    lease_db_exists: bool
    lease_ever_created: bool
    builder_outcome_recorded: bool
    builder_outcome_state: str | None


def diagnose_claim_lifecycle(
    *, claims_path: Path, lease_path: Path, slice_id: str
) -> ClaimLifecycleDiagnosis:
    """Distinguishes CLAIMED / INVOCATION_RESERVED / RESULT_RECORDED
    (via the real inspect_claims) from whether a worker lease was ever
    created and whether a builder outcome was ever recorded (via a
    direct, read-only query of the real lease database's own tables --
    the same tables record_slice_outcome/claim_slice_lease write to,
    read here, never written to). Returns everything False/None for a
    slice with no claim at all, rather than raising -- the absence of
    a claim is itself a valid, reportable diagnosis.
    """
    claims = (
        [row for row in inspect_claims(claims_path) if row["slice_id"] == slice_id]
        if claims_path.is_file()
        else []
    )

    claim_exists = bool(claims)
    claim_state = claims[0]["state"] if claims else None
    claim_token = claims[0]["claim_token"] if claims else None
    acceptance_event_id = claims[0]["acceptance_event_id"] if claims else None

    lease_db_exists = lease_path.is_file()
    lease_ever_created = False
    builder_outcome_recorded = False
    builder_outcome_state: str | None = None

    if lease_db_exists:
        db = sqlite3.connect(lease_path)
        try:
            lease_row = db.execute(
                "SELECT 1 FROM slice_lease_events "
                "WHERE slice_id=? AND event_type='SLICE_LEASED'",
                (slice_id,),
            ).fetchone()
            lease_ever_created = lease_row is not None
            outcome_row = db.execute(
                "SELECT state FROM slice_outcomes WHERE slice_id=?", (slice_id,)
            ).fetchone()
            if outcome_row is not None:
                builder_outcome_recorded = True
                builder_outcome_state = outcome_row[0]
        except sqlite3.OperationalError:
            pass
        finally:
            db.close()

    return ClaimLifecycleDiagnosis(
        slice_id=slice_id,
        claim_exists=claim_exists,
        claim_state=claim_state,
        claim_token=claim_token,
        acceptance_event_id=acceptance_event_id,
        lease_db_exists=lease_db_exists,
        lease_ever_created=lease_ever_created,
        builder_outcome_recorded=builder_outcome_recorded,
        builder_outcome_state=builder_outcome_state,
    )
