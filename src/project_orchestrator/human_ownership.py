"""Fail-closed ownership gate for the Azathoth human-control marker."""
from __future__ import annotations

import json
from pathlib import Path

HUMAN_MODE_FILENAME = "human-mode.json"
AUTHORIZED = "AUTHORIZED"
OWNER_CONTROL_ACTIVE = "OWNER_CONTROL_ACTIVE"
OWNER_CONTROL_UNVERIFIED = "OWNER_CONTROL_UNVERIFIED"


def human_mode_path(state_root: Path) -> Path:
    return state_root / HUMAN_MODE_FILENAME


def owner_control_status(state_root: Path) -> str:
    """Resolve the exact marker without executing any remote command.

    A confirmed absent marker or a well-formed JSON object with literal
    ``enabled: false`` authorizes work. Any other marker or unreadable state
    vetoes work. Never create, remove, or rewrite the owner's marker here.
    """
    try:
        raw = human_mode_path(state_root).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return AUTHORIZED
    except OSError:
        return OWNER_CONTROL_UNVERIFIED

    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return OWNER_CONTROL_UNVERIFIED
    if not isinstance(value, dict) or type(value.get("enabled")) is not bool:
        return OWNER_CONTROL_UNVERIFIED
    return OWNER_CONTROL_ACTIVE if value["enabled"] else AUTHORIZED


def human_mode_active(state_root: Path) -> bool:
    """Fail closed unless the exact marker is confirmed absent or disabled."""
    return owner_control_status(state_root) != AUTHORIZED
