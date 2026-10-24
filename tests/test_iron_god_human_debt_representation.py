from __future__ import annotations

import pytest

from project_orchestrator.iron_god_everward_adapter import (
    HUMAN_DEBT_CATEGORIES as EVERWARD_HUMAN_DEBT,
)
from project_orchestrator.iron_god_everward_adapter import (
    SUBJECTIVE_VISUAL_GAMEPLAY_EVALUATION,
    SUCCESSFUL_UNREAL_BUILD,
)
from project_orchestrator.iron_god_human_debt_representation import (
    PENDING_SIGN_OFF,
    HumanDebtRepresentationError,
    extract_human_debt,
)
from project_orchestrator.iron_god_product_reality_adapter import (
    FAIL,
    HUMAN_DEBT,
    PASS,
    EvidenceCategoryResult,
    ProductRealityEvidence,
)


def _evidence(results: tuple[EvidenceCategoryResult, ...]) -> ProductRealityEvidence:
    return ProductRealityEvidence(
        repository="repo-a", candidate_sha="a" * 40, category_results=results
    )


def test_human_debt_category_extracted_with_pending_sign_off():
    evidence = _evidence(
        (
            EvidenceCategoryResult(
                category="cat-a", status=HUMAN_DEBT, detail="needs eyes"
            ),
        )
    )
    (item,) = extract_human_debt(evidence, frozenset({"cat-a"}))
    assert item.category == "cat-a"
    assert item.status == PENDING_SIGN_OFF
    assert item.automated_detail == "needs eyes"
    assert item.repository == "repo-a"


def test_non_human_debt_category_excluded_regardless_of_status():
    evidence = _evidence(
        (EvidenceCategoryResult(category="cat-b", status=PASS, detail=""),)
    )
    result = extract_human_debt(evidence, frozenset({"cat-a"}))
    assert result == ()


def test_declared_subjective_category_reported_pass_raises():
    evidence = _evidence(
        (EvidenceCategoryResult(category="cat-a", status=PASS, detail=""),)
    )
    with pytest.raises(HumanDebtRepresentationError):
        extract_human_debt(evidence, frozenset({"cat-a"}))


def test_declared_subjective_category_reported_fail_raises():
    evidence = _evidence(
        (EvidenceCategoryResult(category="cat-a", status=FAIL, detail=""),)
    )
    with pytest.raises(HumanDebtRepresentationError):
        extract_human_debt(evidence, frozenset({"cat-a"}))


def test_multiple_human_debt_categories_all_extracted():
    evidence = _evidence(
        (
            EvidenceCategoryResult(category="cat-a", status=HUMAN_DEBT, detail="a"),
            EvidenceCategoryResult(category="cat-b", status=HUMAN_DEBT, detail="b"),
            EvidenceCategoryResult(category="cat-c", status=PASS, detail="c"),
        )
    )
    result = extract_human_debt(evidence, frozenset({"cat-a", "cat-b"}))
    assert {item.category for item in result} == {"cat-a", "cat-b"}


def test_empty_evidence_yields_empty_result():
    assert extract_human_debt(_evidence(()), frozenset({"cat-a"})) == ()


def test_empty_human_debt_categories_yields_empty_result():
    evidence = _evidence(
        (EvidenceCategoryResult(category="cat-a", status=HUMAN_DEBT, detail=""),)
    )
    assert extract_human_debt(evidence, frozenset()) == ()


def test_composes_with_real_everward_human_debt_categories():
    evidence = _evidence(
        (
            EvidenceCategoryResult(
                category=SUCCESSFUL_UNREAL_BUILD, status=PASS, detail="build ok"
            ),
            EvidenceCategoryResult(
                category=SUBJECTIVE_VISUAL_GAMEPLAY_EVALUATION,
                status=HUMAN_DEBT,
                detail="awaiting human review of gameplay footage",
            ),
        )
    )
    (item,) = extract_human_debt(evidence, EVERWARD_HUMAN_DEBT)
    assert item.category == SUBJECTIVE_VISUAL_GAMEPLAY_EVALUATION
    assert item.status == PENDING_SIGN_OFF
