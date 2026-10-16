from __future__ import annotations

import inspect

import pytest

from project_orchestrator.iron_god_everward_adapter import (
    HUMAN_DEBT_CATEGORIES,
    KNOWN_CATEGORIES,
    SUBJECTIVE_VISUAL_GAMEPLAY_EVALUATION,
    EverwardAdapter,
)
from project_orchestrator.iron_god_product_reality_adapter import ProductRealityAdapter


def test_adapter_satisfies_protocol():
    assert isinstance(EverwardAdapter(), ProductRealityAdapter)


def test_known_categories_has_ten_unique_entries():
    assert len(KNOWN_CATEGORIES) == 10
    assert len(set(KNOWN_CATEGORIES)) == 10


def test_human_debt_categories_is_exactly_the_subjective_category():
    assert HUMAN_DEBT_CATEGORIES == frozenset({SUBJECTIVE_VISUAL_GAMEPLAY_EVALUATION})
    assert HUMAN_DEBT_CATEGORIES.issubset(set(KNOWN_CATEGORIES))


def test_deterministic_categories_are_not_human_debt():
    deterministic = set(KNOWN_CATEGORIES) - HUMAN_DEBT_CATEGORIES
    assert len(deterministic) == 9
    assert SUBJECTIVE_VISUAL_GAMEPLAY_EVALUATION not in deterministic


def test_evaluate_raises_naming_repository():
    adapter = EverwardAdapter()
    with pytest.raises(NotImplementedError, match="repo-a"):
        adapter.evaluate("repo-a", "a" * 40)


def test_module_contains_no_subprocess_or_network_import():
    import project_orchestrator.iron_god_everward_adapter as module

    source = inspect.getsource(module)
    forbidden = ("subprocess", "socket", "requests", "httpx", "urllib")
    for token in forbidden:
        assert token not in source, f"module must not import/use {token!r}"
