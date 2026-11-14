"""One temp root for everything the engine runs (owner, 2026-11-14).

Each test run the engine starts gets its own TEMP/TMP directory. Those directories used to sit beside whatever was being
tested (``<worktree>.tmp``, ``<worktree>.quick.tmp``, ``<packet>.tmp`` ...), scattered across the engine's work areas.
The owner's security software writes into every directory used as a temp root (measured: Norton creates ``_norton_``
there whenever a Python process starts) and can only be told to ignore a folder, not a pattern. With
``portfolio_rotation.engine_temp_root`` set, every one of those directories lives under that single folder instead, so
one exclusion covers all of them. Unset, nothing changes.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV = "IRON_GOD_TEMP_ROOT"


def configure(value: object) -> str | None:
    """Apply the policy's temp root to this process and everything it starts; returns the folder in use, if any."""
    text = os.path.expandvars(str(value)).strip() if isinstance(value, str) else ""
    if not text or "%" in text or "$" in text:  # unset, or a variable that did not resolve: keep the old placement
        os.environ.pop(ENV, None)
        return None
    os.environ[ENV] = text
    return text


def temp_dir(beside: Path) -> Path:
    """Where a run's private temp directory goes: under the configured root if there is one, else ``beside`` as before.

    The directory keeps its own name, which already identifies the worktree or packet it belongs to.
    """
    root = os.environ.get(ENV, "").strip()
    return Path(root) / beside.name if root else beside
