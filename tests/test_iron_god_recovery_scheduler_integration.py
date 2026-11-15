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
