"""Fail-closed human ownership gate for the canonical Azathoth control marker."""
from __future__ import annotations

import json
from pathlib import Path

HUMAN_MODE_FILENAME = "human-mode.json"


def human_mode_path(state_root: Path) -> Path:
    return state_root / HUMAN_MODE_FILENAME


def human_mode_active(state_root: Path) -> bool:
    """Only a verified explicit enabled:false marker releases human control.

    A missing legacy marker also permits operation. Any existing marker that
    cannot be read, decoded, or validated is treated as owner-controlled.
    In particular, string "false", null, and empty objects are not consent.
    """
    path = human_mode_path(state_root)
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return False
    except (OSError, UnicodeError):
        return True
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError, TypeError):
        return True
    return not (isinstance(payload, dict) and payload.get("enabled") is False)
