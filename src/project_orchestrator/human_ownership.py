"""Single fail-closed ownership gate for Jeremy-vs-Azathoth laptop control."""

from __future__ import annotations

import json
import os
from pathlib import Path


HUMAN_MODE_FILENAME = "human-mode.json"

AUTHORIZED = "AUTHORIZED"
OWNER_CONTROL_ACTIVE = "OWNER_CONTROL_ACTIVE"
OWNER_CONTROL_UNVERIFIED = "OWNER_CONTROL_UNVERIFIED"


def human_mode_path(state_root: Path) -> Path:
    return state_root / HUMAN_MODE_FILENAME


def owner_state_root(state_path: Path) -> Path:
    """Resolve owner authority for top-level or nested per-worker state."""
    direct = state_path.parent
    for parent in state_path.parents:
        if parent.name == "flow-state":
            return parent
    if human_mode_path(direct).exists():
        return direct
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "ExponentialEngine" / "flow-state"
    return direct


def owner_control_status(state_root: Path) -> str:
    """Return tri-state owner authority from the persistent JSON marker.

    A missing marker is unverified and fails closed. A readable marker
    authorizes automation only when enabled is the literal boolean False.
    Malformed or schema-drifted markers also fail closed.
    """
    path = human_mode_path(state_root)
    if not path.exists() or path.is_symlink() or not path.is_file():
        return OWNER_CONTROL_UNVERIFIED
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return OWNER_CONTROL_UNVERIFIED
    if not isinstance(payload, dict) or type(payload.get("enabled")) is not bool:
        return OWNER_CONTROL_UNVERIFIED
    return OWNER_CONTROL_ACTIVE if payload["enabled"] else AUTHORIZED


def human_mode_active(state_root: Path) -> bool:
    """Block autonomous work unless owner authority is explicitly released."""
    return owner_control_status(state_root) != AUTHORIZED
