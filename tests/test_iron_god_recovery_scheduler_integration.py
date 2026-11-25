"""Scheduler can continue another approved repository when selected task cannot route."""
from types import SimpleNamespace

from project_orchestrator import iron_god_product_lane as lane


def test_routable_alternative_skips_blocked_and_unmeasured(monkeypatch):
    def route(level, profiles, free, *, after_pressure):
        return SimpleNamespace(model="ollama" if level == 1 else None)
    monkeypatch.setattr(lane.model_routing, "route", route)
    items = {"a": {"level": 3}, "b": {"level": 2}, "c": {"level": 1}}
    assert lane.next_routable_local_repository(
        ["a", "b", "c"], set(), items, {}, 8.0,
        after_pressure=False, exclude="a") == "c"
    assert lane.next_routable_local_repository(
        ["a", "b", "c"], {"c"}, items, {}, 8.0,
        after_pressure=False, exclude="a") is None


def test_only_backlog_selected_items_are_candidates(monkeypatch):
    monkeypatch.setattr(lane.model_routing, "route",
                        lambda *args, **kwargs: SimpleNamespace(model="ollama"))
    assert lane.next_routable_local_repository(
        ["blocked", "not-in-items", "approved"], {"blocked"},
        {"approved": {"level": 1}}, {}, 8.0,
        after_pressure=False, exclude="current") == "approved"


def test_same_repository_second_item_is_routable_without_resetting_failed_work(monkeypatch):
    from project_orchestrator import iron_god_backlog as backlog

    monkeypatch.setattr(lane.model_routing, "route",
                        lambda level, *a, **k: SimpleNamespace(model="ollama" if level == 1 else None))
    first = {"id": "first", "repository": "a", "status": "approved",
             "assignee": backlog.AGENT_ID, "level": 2, "depends_on": []}
    spent = {**first, "id": "spent", "level": 1}
    unapproved = {**first, "id": "unapproved", "level": 1, "status": "proposed"}
    blocked = {**first, "id": "blocked", "level": 1, "depends_on": ["missing"]}
    other = {**first, "id": "other", "level": 1}
    state = {"items": {"spent": {"attempts": backlog.MAX_ITEM_ATTEMPTS,
                                  "outcome": "ENVIRONMENT_FAILURE: TIMEOUT"}}}
    items = [first, spent, unapproved, blocked, other]
    kwargs = dict(max_level=2, profiles={}, free_gib=8.0, after_pressure=False, exclude_item="first")
    assert lane.next_routable_backlog_item(["a"], set(), items, state, **kwargs) == ("a", other)
    assert lane.next_routable_backlog_item(["a"], {"a"}, items, state, **kwargs) is None
    assert lane.next_routable_backlog_item(["a"], set(), items[:-1], state, **kwargs) is None
    assert state["items"]["spent"]["attempts"] == backlog.MAX_ITEM_ATTEMPTS


def test_final_eligibility_recheck_rejects_changed_canonical_state():
    from project_orchestrator import iron_god_backlog as backlog

    item = {"id": "candidate", "repository": "a", "status": "approved",
            "assignee": backlog.AGENT_ID, "level": 1, "depends_on": []}
    state = {"items": {}}
    assert lane.final_backlog_exclusion(item, [item], state, max_level=1) is None
    for outcome, attempts, reason in (
        ("MERGED", 1, "ALREADY_COMPLETE"),
        ("DISPATCHED", 1, "IN_FLIGHT"),
        ("ENVIRONMENT_FAILURE: TIMEOUT", backlog.MAX_ITEM_ATTEMPTS,
         "ENVIRONMENT_ATTEMPTS_EXHAUSTED"),
        ("FAIL: SLICE_TESTS_FAILED", backlog.MAX_ITEM_ATTEMPTS, "ATTEMPTS_EXHAUSTED"),
    ):
        state = {"items": {"candidate": {"outcome": outcome, "attempts": attempts}}}
        assert lane.final_backlog_exclusion(item, [item], state, max_level=1)[0] == reason


def test_final_eligibility_rechecks_dependency_and_ownership():
    from project_orchestrator import iron_god_backlog as backlog

    dependency = {"id": "dep", "repository": "a", "status": "approved",
                  "assignee": backlog.AGENT_ID, "level": 1, "depends_on": []}
    item = {**dependency, "id": "candidate", "depends_on": ["dep"]}
    state = {"items": {"dep": {"outcome": "MERGED"}}}
    assert lane.final_backlog_exclusion(item, [dependency, item], state, max_level=1) is None
    assert lane.final_backlog_exclusion(item, [dependency, item], {"items": {}}, max_level=1)[0] == (
        "WAITING_ON_DEPENDENCY")
    other = {**item, "assignee": "claude-code"}
    assert lane.final_backlog_exclusion(other, [dependency, other], state, max_level=1)[0] == (
        "ASSIGNED_ELSEWHERE")
