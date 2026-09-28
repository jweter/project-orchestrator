"""Single fail-closed ownership gate for Jeremy-vs-Azathoth laptop control."""
from __future__ import annotations

from pathlib import Path


HUMAN_MODE_FILENAME = "human-mode.json"


def human_mode_path(state_root: Path) -> Path:
    return state_root / HUMAN_MODE_FILENAME


def human_mode_active(state_root: Path) -> bool:
    """Presence of the ownership marker always gives the laptop to the human.

    FULL_POWER is the only supported transition back to autonomous ownership and
    must explicitly remove the marker. Contents are deliberately irrelevant so
    partial writes or schema drift cannot accidentally re-enable workers.
    """
    return human_mode_path(state_root).exists()
