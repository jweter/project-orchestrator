"""Offline scheduling policy tests; no workers, subprocesses or real claims."""
import pytest

from project_orchestrator.development_execution_policy import (
    Denial,
    DevelopmentTask,
    TaskState,
    select_independent_task,
)


def test_denied_operation_never_retried_under_different_task_id():
    tasks = [
        DevelopmentTask("retry-via-another-tool", "write-bridge", priority=100),
        DevelopmentTask("independent-lint", "lint-other-module", priority=1),
    ]
    chosen = select_independent_task(
        tasks, denied=[Denial("write-bridge", "TOOL_SAFETY_DENIED")]
    )
    assert chosen.task_id == "independent-lint"


def test_no_eligible_work_returns_none():
    assert select_independent_task(
        [DevelopmentTask("blocked", "denied-op")],
        denied=[Denial("denied-op", "TOOL_SAFETY_DENIED")],
    ) is None


def test_dependencies_must_be_completed():
    tasks = [
        DevelopmentTask("dependent", "op-a", dependencies=("missing",), priority=100),
        DevelopmentTask("independent", "op-b"),
    ]
    assert select_independent_task(tasks).task_id == "independent"
    assert select_independent_task(tasks, completed=frozenset({"missing"})).task_id == "dependent"


def test_blocked_and_completed_tasks_not_selected():
    tasks = [
        DevelopmentTask("blocked", "op-a", state=TaskState.BLOCKED, priority=10),
        DevelopmentTask("done", "op-b", state=TaskState.DONE, priority=9),
        DevelopmentTask("ready", "op-c"),
    ]
    assert select_independent_task(tasks).task_id == "ready"


def test_duplicate_task_identity_fails_closed():
    with pytest.raises(ValueError, match="DUPLICATE_TASK_ID"):
        select_independent_task([
            DevelopmentTask("same", "op-a"),
            DevelopmentTask("same", "op-b"),
        ])
