"""Malformed backlog roots must not silently masquerade as an empty queue."""

import json

import pytest

from project_orchestrator.iron_god_backlog import BACKLOG_PATH, load_backlog


@pytest.mark.parametrize(
    ("payload", "problem"),
    [
        ([], "BACKLOG_ROOT_NOT_AN_OBJECT"),
        (None, "BACKLOG_ROOT_NOT_AN_OBJECT"),
        ({}, "BACKLOG_ITEMS_MISSING"),
        ({"schema_version": 1}, "BACKLOG_ITEMS_MISSING"),
    ],
)
def test_invalid_root_is_reported(tmp_path, payload, problem):
    path = tmp_path / BACKLOG_PATH
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")

    items, rejected = load_backlog(tmp_path)

    assert items == []
    assert rejected == {"<backlog>": [problem]}


def test_explicit_empty_items_remains_valid(tmp_path):
    path = tmp_path / BACKLOG_PATH
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"items": []}), encoding="utf-8")

    assert load_backlog(tmp_path) == ([], {})
