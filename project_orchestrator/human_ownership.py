"""Single fail-closed ownership gate for Jeremy-vs-Azathoth laptop control."""
from __future__ import annotations

import json
from pathlib import Path


HUMAN_MODE_FILENAME = "human-mode.json"


def human_mode_path(state_root: Path) -> Path:
    return state_root / HUMAN_MODE_FILENAME


def human_mode_active(state_root: Path) -> bool:
    """Return True whenever a human-ownership marker exists.

    Presence is authoritative. Malformed or partially-written markers fail closed
    to human ownership; FULL_POWER must explicitly remove the marker.
    """
    path = human_mode_path(state_root)
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return True
    return not isinstance(payload, dict) or payload.get("enabled") is not False
