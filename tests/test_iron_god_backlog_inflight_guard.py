"""An in-flight backlog packet must not be selected for duplicate dispatch."""

import pytest

from project_orchestrator import iron_god_backlog as backlog


def _item():
    item = {
        name: "Concrete authorized product capability" for name in backlog.REQUIRED_TEXT
    }
    item.update({name: [] for name in backlog.REQUIRED_LISTS})
    item.update(
        id="KEC-INFLIGHT",
        repository="jweter/knowledge-engine-core",
        status="approved",
        level=1,
        assignee="iron-god",
        build={
            "module_path": "src/example.py",
            "function_name": "example",
            "signature": "example(value: int) -> int",
            "objective": "Correct existing behavior",
            "examples": ["example(1) == 1"],
        },
    )
    return item


@pytest.mark.parametrize("outcome", sorted(backlog.IN_FLIGHT))
def test_inflight_item_is_not_redispatched(outcome):
    item = _item()
    assert backlog.item_problems(item) == []
    state = {
        "items": {
            item["id"]: {"attempts": 1, "packets": ["packet-1"], "outcome": outcome}
        }
    }

    assert backlog.next_item([item], state, item["repository"], max_level=2) is None


def test_unstarted_item_remains_eligible():
    item = _item()
    assert (
        backlog.next_item([item], {"items": {}}, item["repository"], max_level=2)
        == item
    )
