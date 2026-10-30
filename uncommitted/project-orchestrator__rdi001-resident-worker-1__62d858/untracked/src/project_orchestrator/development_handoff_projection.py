"""Pure adapter from verified controller handoff metadata to safe task selection.

Does not claim, launch, change controller state, or provision worktrees.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from .development_execution_policy import DevelopmentTask, TaskState
from .development_selection_boundary import select_from_verified_history


def project_handoffs(handoffs: Iterable[Mapping[str, object]]) -> tuple[DevelopmentTask, ...]:
    tasks = []
    for handoff in handoffs:
        if not isinstance(handoff, Mapping):
            raise ValueError("HANDOFF_UNVERIFIED")
        task_id = handoff.get("id")
        operation_id = handoff.get("operation_id")
        dependencies = handoff.get("dependencies", ())
        priority = handoff.get("priority", 0)
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("HANDOFF_ID_UNVERIFIED")
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("HANDOFF_OPERATION_ID_UNVERIFIED")
        if (
            not isinstance(dependencies, (list, tuple))
            or any(not isinstance(item, str) or not item.strip() for item in dependencies)
            or isinstance(priority, bool)
            or not isinstance(priority, int)
        ):
            raise ValueError("HANDOFF_METADATA_UNVERIFIED")
        tasks.append(DevelopmentTask(
            task_id=task_id,
            operation_id=operation_id,
            dependencies=tuple(dependencies),
            state=TaskState.READY,
            priority=priority,
        ))
    return tuple(tasks)


def select_handoff(
    handoffs: Iterable[Mapping[str, object]],
    *,
    history_path: Path,
    completed: frozenset[str] = frozenset(),
) -> DevelopmentTask | None:
    """Never infer an operation identity or assume an absent denial store is safe."""
    return select_from_verified_history(
        project_handoffs(handoffs), history_path=history_path, completed=completed
    )
