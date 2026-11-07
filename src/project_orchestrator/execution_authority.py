"""Single fail-closed decision: may autonomous work start right now?

Owner decision (2026-10-25): the authoritative owner controller
(``iron_god_owner_control``) is the only execution authority. Work is
permitted only for a fresh owner Take the Wheel that a live supervisor has
acknowledged, for the exact accepted release the caller is running. A
pending, expired, revoked, mis-bound or unauthenticated grant denies.

The legacy IronGodLocal ``controller-handoff-state.json`` flag and the
retired owner marker are not inputs. This module only reads; it never
writes controller state or grants authority. A resident process is not
authority: callers must re-evaluate before each unit of work.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from . import iron_god_owner_control as _controller

OWNER_RELEASE_UNPINNED = "EXECUTION_RELEASE_UNPINNED"


@dataclass(frozen=True)
class ExecutionDecision:
    permitted: bool
    reasons: tuple[str, ...]
    supervisor_instance_id: str | None = None
    generation: int | None = None


def _from_owner(decision: _controller.OwnerDecision) -> ExecutionDecision:
    permitted = decision.permitted is True and decision.status == _controller.AUTHORIZED
    reasons = decision.reasons or (() if permitted else ("EXECUTION_AUTHORITY_DENIED",))
    return ExecutionDecision(
        permitted=permitted,
        reasons=tuple(reasons),
        supervisor_instance_id=decision.supervisor_instance_id,
        generation=decision.generation,
    )


def evaluate_execution_authority(
    control_root: Path | None = None,
    *,
    release_sha: str | None,
    supervisor: _controller.SupervisorIdentity | None = None,
    liveness: Callable[[_controller.SupervisorIdentity], bool] = _controller.supervisor_alive,
) -> ExecutionDecision:
    """Fresh decision for a caller running ``release_sha``.

    Supervisors pass their own identity, so only the acknowledging process is
    permitted; workers pass ``supervisor=None`` and are permitted while the
    acknowledging supervisor is alive.
    """
    if not isinstance(release_sha, str) or not release_sha:
        return ExecutionDecision(permitted=False, reasons=(OWNER_RELEASE_UNPINNED,))
    try:
        root = control_root if control_root is not None else _controller.control_root()
        decision = _controller.evaluate(
            root, release_sha=release_sha, supervisor=supervisor, liveness=liveness
        )
    except Exception:  # Any failure to establish authority denies.
        return ExecutionDecision(permitted=False, reasons=("EXECUTION_AUTHORITY_UNVERIFIED",))
    return _from_owner(decision)


def acknowledge_and_evaluate(
    control_root: Path,
    supervisor: _controller.SupervisorIdentity,
    *,
    release_sha: str | None,
    liveness: Callable[[_controller.SupervisorIdentity], bool] = _controller.supervisor_alive,
) -> ExecutionDecision:
    """Supervisor path: acknowledge a fresh pending grant, then decide."""
    if not isinstance(release_sha, str) or not release_sha:
        return ExecutionDecision(permitted=False, reasons=(OWNER_RELEASE_UNPINNED,))
    try:
        decision = _controller.acknowledge_grant(
            control_root, supervisor, release_sha=release_sha, liveness=liveness,
            safety_check=lambda: _resume_safety_gate(control_root, release_sha),
        )
    except Exception:
        return ExecutionDecision(permitted=False, reasons=("EXECUTION_AUTHORITY_UNVERIFIED",))
    if not decision.permitted and decision.owner_intent == _controller.TAKE_THE_WHEEL:
        _flag_owner_attention(control_root, decision.reasons, release_sha)
    return _from_owner(decision)


def _resume_safety_gate(control_root: Path, release_sha: str) -> str | None:
    """Reason a supervisor may not resume the owner's persistent grant, else None.

    The running release must be the accepted, receipted release pinned beside the controller's flow-state.
    """
    try:
        pin = json.loads((Path(control_root).parent / "supervisor-pinned-release.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "PIN_UNREADABLE"
    if not isinstance(pin, dict) or pin.get("enabled") is not True or pin.get("accepted_sha") != release_sha:
        return "RELEASE_NOT_THE_PINNED_RELEASE"
    receipt = pin.get("receipt_path")
    if not isinstance(receipt, str) or not Path(receipt).is_file():
        return "RELEASE_RECEIPT_MISSING"
    return None


def _flag_owner_attention(control_root: Path, reasons: tuple[str, ...], release_sha: str) -> None:
    """Fail closed loudly: the owner's Take the Wheel is preserved but cannot be resumed safely."""
    try:
        from .state_io import atomic_json

        atomic_json(Path(control_root).parent / "owner-attention.json", {
            "schema_version": 1, "kind": "TAKE_THE_WHEEL_SUSPENDED", "release_sha": release_sha,
            "reasons": list(reasons), "observed_at_utc": datetime.now(UTC).isoformat(),
            "action_needed": "Owner intent is preserved; execution is suspended until the reasons clear or you re-issue Take the Wheel."})
    except Exception:  # noqa: BLE001 - attention is best effort and never grants anything
        pass


def supervisor_identity(worker: dict[str, Any]) -> _controller.SupervisorIdentity:
    """Build the controller identity from the supervisor instance-lock record."""
    return _controller.SupervisorIdentity.from_mapping(worker)
