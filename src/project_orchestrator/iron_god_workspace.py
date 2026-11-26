"""Canonical workspace layout; no migration, deletion or security-policy changes."""
from __future__ import annotations

from pathlib import Path


def canon_workspace(state_root: Path) -> Path:
    """Keep persistent repository data at its existing location."""
    return Path(state_root).parent / "canon-workspaces"


def product_workspace(state_root: Path) -> Path:
    return canon_workspace(state_root) / "products"


def product_worktrees(state_root: Path) -> Path:
    return product_workspace(state_root) / "worktrees"
