"""Pure development-task scheduling policy; no process, filesystem, or Git effects.

A denied operation is terminal for its operation identity. Independent work may
continue, but the denied operation cannot be retried through another transport.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class TaskState(str, Enum):
    READY = "READY"
    BLOCKED = "BLOCKED"
    DONE = "DONE"


@dataclass(frozen=True)
class DevelopmentTask:
    task_id: str
    operation_id: str
    dependencies: tuple[str, ...] = ()
    state: TaskState = TaskState.READY
    priority: int = 0


@dataclass(frozen=True)
class Denial:
    operation_id: str
    reason: str


def select_independent_task(
    tasks: Iterable[DevelopmentTask],
    *,
    completed: frozenset[str] = frozenset(),
    denied: Iterable[Denial] = (),
) -> DevelopmentTask | None:
    """Select only an independent task whose operation has never been denied."""
    tasks = tuple(tasks)
    if len({task.task_id for task in tasks}) != len(tasks):
        raise ValueError("DUPLICATE_TASK_ID")
    blocked_operations = {entry.operation_id for entry in denied}
    eligible = (
        task for task in tasks
        if task.state is TaskState.READY
        and task.operation_id not in blocked_operations
        and all(dependency in completed for dependency in task.dependencies)
    )
    return next(iter(sorted(eligible, key=lambda task: (-task.priority, task.task_id))), None)
