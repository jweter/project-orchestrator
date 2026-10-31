"""Planner-supplied list entries must be validated before dispatch or ownership checks."""

import json

import pytest

from project_orchestrator import iron_god_backlog as backlog


def _approved_item():
    item = {name: "A concrete example specification" for name in backlog.REQUIRED_TEXT}
    item.update({name: [] for name in backlog.REQUIRED_LISTS})
    item.update(
        id="KEC-MALFORMED-LIST",
        status="approved",
        level=2,
        assignee="iron-god",
        direct={
            "module_path": "src/example.py",
            "targets": ["example"],
            "direction": "Change the existing function safely",
            "test_path": "tests/test_example.py",
            "test_source": "def test_example(): assert True",
        },
    )
    return item


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("interfaces", {"file": "src/example.py"}),
        ("depends_on", {"id": "KEC-OTHER"}),
        ("acceptance_criteria", {"claim": "works"}),
        ("tests", 42),
        ("interfaces", "   "),
    ],
)
def test_invalid_list_entries_are_rejected(field, invalid):
    item = _approved_item()
    item[field] = [invalid]
    assert f"{field.upper()}_ENTRY_INVALID" in backlog.item_problems(item)


def test_unhashable_dependency_fails_closed_before_dispatch(tmp_path):
    item = _approved_item()
    item["depends_on"] = [{"id": "KEC-OTHER"}]
    path = tmp_path / backlog.BACKLOG_PATH
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"items": [item]}), encoding="utf-8")

    items, rejected = backlog.load_backlog(tmp_path)

    assert items == []
    assert "DEPENDS_ON_ENTRY_INVALID" in rejected[item["id"]]
    assert (
        backlog.next_item(items, {"items": {}}, item["repository"], max_level=2) is None
    )
