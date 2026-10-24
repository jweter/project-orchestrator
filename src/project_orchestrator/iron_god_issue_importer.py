"""Issue/task importer (roadmap section 3.1 / Phase 6: Capability
Compiler).

Section 3.1 lists "GitHub issues" and "local issue mirrors" among the
Capability Compiler's inputs. GitHub fetch operations are out of
scope for this module -- it imports from an already-in-memory list of
mirrored issue/task records (however they got there is someone else's
concern) and normalizes them into a typed, read-only
ImportedTask/ImportedTaskBatch representation for later Capability
Compiler stages to consume.

Fails closed rather than guessing: a malformed record (missing or
wrong-typed required field, an unrecognized state) raises, and a
duplicate id within the same repository raises rather than silently
picking a winner. This module performs no file, network, or state
mutation itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

OPEN = "open"
CLOSED = "closed"
KNOWN_STATES = frozenset({OPEN, CLOSED})

_REQUIRED_STRING_FIELDS = ("id", "title", "repository")


class IssueImportError(RuntimeError):
    """Raised when an issue/task record is malformed or ambiguous."""


@dataclass(frozen=True)
class ImportedTask:
    task_id: str
    title: str
    repository: str
    state: str
    actionable: bool
    labels: tuple[str, ...]
    body: str


@dataclass(frozen=True)
class ImportedTaskBatch:
    tasks: tuple[ImportedTask, ...]


def _require_nonempty_string(record: dict[str, Any], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise IssueImportError(
            f"record {record!r} is missing a non-empty string field {field!r}"
        )
    return value


def _optional_string_list(record: dict[str, Any], field: str) -> tuple[str, ...]:
    value = record.get(field, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise IssueImportError(
            f"record {record!r} field {field!r} must be a list of strings"
        )
    return tuple(value)


def import_local_issues(records: list[dict[str, Any]]) -> ImportedTaskBatch:
    """Normalize `records` (already-mirrored local issue/task dicts)
    into a typed, read-only ImportedTaskBatch.

    Raises IssueImportError for any malformed record or for a
    duplicate id within the same repository. The same id in two
    different repositories is not a conflict.
    """
    tasks: list[ImportedTask] = []
    seen_ids_by_repository: dict[str, set[str]] = {}

    for record in records:
        if not isinstance(record, dict):
            raise IssueImportError(f"record must be an object, got {record!r}")

        for field in _REQUIRED_STRING_FIELDS:
            _require_nonempty_string(record, field)

        task_id = record["id"]
        title = record["title"]
        repository = record["repository"]
        state = record.get("state")
        if state not in KNOWN_STATES:
            raise IssueImportError(
                f"record {task_id!r} has unrecognized state {state!r}; "
                f"must be one of {sorted(KNOWN_STATES)}"
            )

        repo_ids = seen_ids_by_repository.setdefault(repository, set())
        if task_id in repo_ids:
            raise IssueImportError(
                f"duplicate id {task_id!r} within repository {repository!r}"
            )
        repo_ids.add(task_id)

        body = record.get("body", "")
        if not isinstance(body, str):
            raise IssueImportError(f"record {task_id!r} field 'body' must be a string")

        tasks.append(
            ImportedTask(
                task_id=task_id,
                title=title,
                repository=repository,
                state=state,
                actionable=state == OPEN,
                labels=_optional_string_list(record, "labels"),
                body=body,
            )
        )

    return ImportedTaskBatch(tasks=tuple(tasks))
