from __future__ import annotations

import pytest

from project_orchestrator.iron_god_issue_importer import (
    ImportedTaskBatch,
    IssueImportError,
    import_local_issues,
)


def _record(**overrides):
    base = {
        "id": "ISS-1",
        "title": "Fix the thing",
        "repository": "repo-a",
        "state": "open",
    }
    base.update(overrides)
    return base


def test_open_record_is_actionable():
    batch = import_local_issues([_record()])
    assert isinstance(batch, ImportedTaskBatch)
    (task,) = batch.tasks
    assert task.task_id == "ISS-1"
    assert task.actionable is True


def test_closed_record_is_not_actionable():
    batch = import_local_issues([_record(state="closed")])
    (task,) = batch.tasks
    assert task.actionable is False


@pytest.mark.parametrize("field", ["id", "title", "repository"])
def test_missing_required_field_raises(field):
    record = _record()
    del record[field]
    with pytest.raises(IssueImportError):
        import_local_issues([record])


@pytest.mark.parametrize("field", ["id", "title", "repository"])
def test_non_string_required_field_raises(field):
    record = _record(**{field: 123})
    with pytest.raises(IssueImportError):
        import_local_issues([record])


def test_unknown_state_raises():
    with pytest.raises(IssueImportError):
        import_local_issues([_record(state="merged")])


def test_duplicate_id_within_same_repository_raises():
    records = [_record(), _record(title="a different title")]
    with pytest.raises(IssueImportError):
        import_local_issues(records)


def test_same_id_in_different_repositories_is_allowed():
    records = [_record(repository="repo-a"), _record(repository="repo-b")]
    batch = import_local_issues(records)
    assert len(batch.tasks) == 2


def test_labels_and_body_default_and_round_trip():
    batch = import_local_issues(
        [_record(labels=["bug", "p1"], body="detailed description")]
    )
    (task,) = batch.tasks
    assert task.labels == ("bug", "p1")
    assert task.body == "detailed description"

    batch_defaults = import_local_issues([_record()])
    (task_defaults,) = batch_defaults.tasks
    assert task_defaults.labels == ()
    assert task_defaults.body == ""


def test_non_list_labels_raises():
    with pytest.raises(IssueImportError):
        import_local_issues([_record(labels="bug")])


def test_empty_input_yields_empty_batch():
    batch = import_local_issues([])
    assert batch.tasks == ()
