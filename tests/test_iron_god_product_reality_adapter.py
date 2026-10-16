from __future__ import annotations

import inspect

import pytest

from project_orchestrator.iron_god_product_reality_adapter import (
    FAIL,
    HUMAN_DEBT,
    PASS,
    EvidenceCategoryResult,
    NotImplementedAdapter,
    ProductRealityAdapter,
    ProductRealityEvidence,
)


def _evidence(statuses: tuple[str, ...]) -> ProductRealityEvidence:
    results = tuple(
        EvidenceCategoryResult(category=f"cat-{i}", status=status, detail="")
        for i, status in enumerate(statuses)
    )
    return ProductRealityEvidence(
        repository="repo-a", candidate_sha="a" * 40, category_results=results
    )


def test_all_pass_is_overall_pass():
    assert _evidence((PASS, PASS)).overall_status == PASS


def test_any_fail_is_overall_fail():
    assert _evidence((PASS, FAIL)).overall_status == FAIL


def test_any_human_debt_with_no_fail_is_overall_human_debt():
    assert _evidence((PASS, HUMAN_DEBT)).overall_status == HUMAN_DEBT


def test_fail_takes_precedence_over_human_debt():
    assert _evidence((FAIL, HUMAN_DEBT)).overall_status == FAIL


def test_empty_evidence_is_fail_never_defaults_to_pass():
    assert _evidence(()).overall_status == FAIL


def test_not_implemented_adapter_satisfies_protocol():
    assert isinstance(NotImplementedAdapter(), ProductRealityAdapter)


def test_not_implemented_adapter_raises_naming_repository():
    adapter = NotImplementedAdapter()
    with pytest.raises(NotImplementedError, match="repo-a"):
        adapter.evaluate("repo-a", "a" * 40)


def test_module_contains_no_subprocess_or_network_import():
    import project_orchestrator.iron_god_product_reality_adapter as module

    source = inspect.getsource(module)
    forbidden = ("subprocess", "socket", "requests", "httpx", "urllib")
    for token in forbidden:
        assert token not in source, f"module must not import/use {token!r}"
