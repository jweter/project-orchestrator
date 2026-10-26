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

from dataclasses import dataclass
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
            control_root, supervisor, release_sha=release_sha, liveness=liveness
        )
    except Exception:
        return ExecutionDecision(permitted=False, reasons=("EXECUTION_AUTHORITY_UNVERIFIED",))
    return _from_owner(decision)


def supervisor_identity(worker: dict[str, Any]) -> _controller.SupervisorIdentity:
    """Build the controller identity from the supervisor instance-lock record."""
    return _controller.SupervisorIdentity.from_mapping(worker)
