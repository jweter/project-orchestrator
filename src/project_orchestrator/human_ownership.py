"""Single fail-closed ownership gate for Jeremy-vs-Azathoth laptop control.

Owner decision (2026-10-25): authority comes only from the authoritative
owner controller (``iron_god_owner_control``). The retired owner marker file
is not read here and grants nothing. ``state_root`` is the engine's
``flow-state`` directory; its controller lives beside it in
``IronGodControl``.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import iron_god_owner_control as _controller

AUTHORIZED = "AUTHORIZED"
OWNER_CONTROL_ACTIVE = "OWNER_CONTROL_ACTIVE"
OWNER_CONTROL_UNVERIFIED = "OWNER_CONTROL_UNVERIFIED"


def owner_state_root(state_path: Path) -> Path:
    """Resolve owner authority for top-level or nested per-worker state."""
    direct = state_path.parent
    for parent in state_path.parents:
        if parent.name == "flow-state":
            return parent
    if _controller.control_root(direct).exists():
        return direct
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "ExponentialEngine" / "flow-state"
    return direct


def owner_control_status(state_root: Path) -> str:
    """Return tri-state owner authority from the authoritative controller.

    AUTHORIZED: a fresh owner Take the Wheel is pending supervisor
    acknowledgment or is acknowledged by a live supervisor.
    OWNER_CONTROL_ACTIVE: the latest owner command is Give Me Control.
    OWNER_CONTROL_UNVERIFIED: anything else, including no controller state,
    failed authentication, expiry, or a dead acknowledging supervisor.
    """
    try:
        return _controller.owner_status(_controller.control_root(Path(state_root)))
    except Exception:  # Any controller failure is unverified authority.
        return OWNER_CONTROL_UNVERIFIED


def human_mode_active(state_root: Path) -> bool:
    """Block autonomous work unless owner authority is explicitly released."""
    return owner_control_status(state_root) != AUTHORIZED
