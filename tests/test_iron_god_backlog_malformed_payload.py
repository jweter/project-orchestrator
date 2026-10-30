"""Malformed planner payloads must be rejected, not crash backlog inspection."""

import json

import pytest

from project_orchestrator import iron_god_backlog as backlog


def _valid_item():
    item = {name: "example specification" for name in backlog.REQUIRED_TEXT}
    item.update({name: [] for name in backlog.REQUIRED_LISTS})
    item.update(id="KEC-VALID", status="proposed", level=3)
    return item


@pytest.mark.parametrize("bad_status", [[], {}, 42])
def test_unhashable_or_nonstr_status_is_rejected(bad_status):
    item = _valid_item()
    item["status"] = bad_status
    assert "STATUS_INVALID" in backlog.item_problems(item)


@pytest.mark.parametrize("bad_criteria", [42, True, {"a": "b"}])
def test_nonlist_acceptance_criteria_is_rejected(bad_criteria):
    item = _valid_item()
    item["acceptance_criteria"] = bad_criteria
    assert "MISSING_ACCEPTANCE_CRITERIA" in backlog.item_problems(item)


@pytest.mark.parametrize("bad_items", [42, {}, "unexpected"])
def test_nonlist_backlog_items_fails_closed(tmp_path, bad_items):
    target = tmp_path / backlog.BACKLOG_PATH
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"items": bad_items}), encoding="utf-8")
    items, rejected = backlog.load_backlog(tmp_path)
    assert items == []
    assert rejected == {"<backlog>": ["BACKLOG_ITEMS_NOT_A_LIST"]}
