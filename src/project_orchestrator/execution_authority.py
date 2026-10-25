"""Single fail-closed decision: may autonomous work start right now?

Two independent inputs must both agree before any worker dispatches:

1. The IronGodLocal controller grants candidate execution: its identity is
   ``iron-god-handoff-v1``, ``mode`` is ``candidate_mode`` and
   ``candidate_execution_authorized`` is the literal ``True``.
2. A verified owner attestation reports ``VERIFIED_RELEASED``.

No supported owner-attestation contract exists yet. The retired owner marker
is not an input to this module and must never become a fallback, so the
production attestation always reports ``OWNER_AUTHORITY_CONTRACT_UNDEFINED``.
Supplying a real attestation provider is the owner-approved cutover step.

This module only reads. It never writes controller state, creates markers,
grants authority, or starts anything. A resident process is not authority.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

CONTROLLER_IDENTITY = "iron-god-handoff-v1"
OWNER_RELEASED = "VERIFIED_RELEASED"
OWNER_AUTHORITY_CONTRACT_UNDEFINED = "OWNER_AUTHORITY_CONTRACT_UNDEFINED"

OwnerAttestation = Callable[[], str]


@dataclass(frozen=True)
class ExecutionDecision:
    permitted: bool
    reasons: tuple[str, ...]


def default_controller_state_path() -> Path | None:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    return (
        Path(local_app_data)
        / "ExponentialEngine"
        / "IronGodLocal"
        / "controller-handoff-state.json"
    )


def undefined_owner_attestation() -> str:
    """Production default until the owner defines a verifiable contract."""
    return OWNER_AUTHORITY_CONTRACT_UNDEFINED


def controller_denials(path: Path | None) -> list[str]:
    if path is None:
        return ["CONTROLLER_STATE_PATH_UNAVAILABLE"]
    try:
        if path.is_symlink() or not path.is_file():
            return ["CONTROLLER_STATE_MISSING_OR_UNTRUSTED"]
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return ["CONTROLLER_STATE_UNREADABLE"]
    if not isinstance(payload, dict) or payload.get("controller") != CONTROLLER_IDENTITY:
        return ["CONTROLLER_IDENTITY_UNVERIFIED"]
    denials = []
    mode = payload.get("mode")
    if mode == "shadow_state_only":
        denials.append("CONTROLLER_SHADOW_MODE")
    elif mode != "candidate_mode":
        denials.append("CONTROLLER_MODE_UNSUPPORTED")
    if payload.get("candidate_execution_authorized") is not True:
        denials.append("CONTROLLER_CANDIDATE_EXECUTION_DISABLED")
    return denials


def evaluate_execution_authority(
    controller_state: Path | None = None,
    owner_attestation: OwnerAttestation = undefined_owner_attestation,
) -> ExecutionDecision:
    """Return a fresh decision; callers must re-evaluate before each unit of work."""
    path = controller_state if controller_state is not None else default_controller_state_path()
    reasons = controller_denials(path)
    try:
        owner = owner_attestation()
    except Exception:  # Any provider failure is unverified authority.
        owner = "OWNER_AUTHORITY_UNVERIFIED"
    if owner != OWNER_RELEASED:
        reasons.append(owner if isinstance(owner, str) and owner else "OWNER_AUTHORITY_UNVERIFIED")
    return ExecutionDecision(permitted=not reasons, reasons=tuple(reasons))
