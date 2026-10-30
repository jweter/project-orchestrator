"""Read-only selection boundary for independently authorized development tasks.

No controller mutation, worker invocation, claims, or fallback on bad history.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .development_denial_store import load_denials
from .development_execution_policy import DevelopmentTask, select_independent_task


def select_from_verified_history(
    tasks: Iterable[DevelopmentTask],
    *,
    history_path: Path,
    completed: frozenset[str] = frozenset(),
) -> DevelopmentTask | None:
    """Select independent work only after successfully reading denial history."""
    ledger = load_denials(history_path)
    return select_independent_task(tasks, completed=completed, denied=ledger.denials)
