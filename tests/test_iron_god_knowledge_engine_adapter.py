from __future__ import annotations

import inspect

import pytest

from project_orchestrator.iron_god_knowledge_engine_adapter import (
    HUMAN_DEBT_CATEGORIES,
    KNOWN_CATEGORIES,
    KnowledgeEngineAdapter,
)
from project_orchestrator.iron_god_product_reality_adapter import ProductRealityAdapter


def test_adapter_satisfies_protocol():
    assert isinstance(KnowledgeEngineAdapter(), ProductRealityAdapter)


def test_known_categories_has_seven_unique_entries():
    assert len(KNOWN_CATEGORIES) == 7
    assert len(set(KNOWN_CATEGORIES)) == 7


def test_human_debt_categories_is_empty_and_subset():
    assert HUMAN_DEBT_CATEGORIES == frozenset()
    assert HUMAN_DEBT_CATEGORIES.issubset(set(KNOWN_CATEGORIES))


def test_evaluate_raises_naming_repository():
    adapter = KnowledgeEngineAdapter()
    with pytest.raises(NotImplementedError, match="repo-a"):
        adapter.evaluate("repo-a", "a" * 40)


def test_module_contains_no_subprocess_or_network_import():
    import project_orchestrator.iron_god_knowledge_engine_adapter as module

    source = inspect.getsource(module)
    forbidden = ("subprocess", "socket", "requests", "httpx", "urllib")
    for token in forbidden:
        assert token not in source, f"module must not import/use {token!r}"
