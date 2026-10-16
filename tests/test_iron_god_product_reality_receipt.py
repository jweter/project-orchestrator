from __future__ import annotations

import pytest

from project_orchestrator.iron_god_product_reality_adapter import (
    FAIL,
    HUMAN_DEBT,
    PASS,
    EvidenceCategoryResult,
    ProductRealityEvidence,
)
from project_orchestrator.iron_god_product_reality_receipt import (
    EvidenceReceiptValidationError,
    build_receipt,
    validate_receipt,
)


def _evidence(statuses: tuple[str, ...]) -> ProductRealityEvidence:
    results = tuple(
        EvidenceCategoryResult(category=f"cat-{i}", status=status, detail=f"d{i}")
        for i, status in enumerate(statuses)
    )
    return ProductRealityEvidence(
        repository="repo-a", candidate_sha="a" * 40, category_results=results
    )


def _payload(receipt) -> dict:
    return {
        "repository": receipt.repository,
        "candidate_sha": receipt.candidate_sha,
        "category_results": [
            {"category": r.category, "status": r.status, "detail": r.detail}
            for r in receipt.category_results
        ],
        "content_hash": receipt.content_hash,
    }


def test_build_then_validate_round_trips():
    receipt = build_receipt(_evidence((PASS, PASS)))
    restored = validate_receipt(_payload(receipt))
    assert restored == receipt


def test_missing_field_raises():
    receipt = build_receipt(_evidence((PASS,)))
    payload = _payload(receipt)
    del payload["candidate_sha"]
    with pytest.raises(EvidenceReceiptValidationError):
        validate_receipt(payload)


def test_empty_category_results_raises():
    receipt = build_receipt(_evidence((PASS,)))
    payload = _payload(receipt)
    payload["category_results"] = []
    with pytest.raises(EvidenceReceiptValidationError):
        validate_receipt(payload)


def test_duplicate_category_raises():
    receipt = build_receipt(_evidence((PASS,)))
    payload = _payload(receipt)
    payload["category_results"].append(dict(payload["category_results"][0]))
    with pytest.raises(EvidenceReceiptValidationError):
        validate_receipt(payload)


def test_tampered_status_causes_hash_mismatch():
    receipt = build_receipt(_evidence((PASS,)))
    payload = _payload(receipt)
    payload["category_results"][0]["status"] = FAIL
    with pytest.raises(EvidenceReceiptValidationError, match="content_hash mismatch"):
        validate_receipt(payload)


def test_evidence_property_reuses_ig080_overall_status_pass():
    receipt = build_receipt(_evidence((PASS, PASS)))
    assert receipt.evidence.overall_status == PASS


def test_evidence_property_reuses_ig080_overall_status_fail_precedence():
    receipt = build_receipt(_evidence((PASS, FAIL, HUMAN_DEBT)))
    assert receipt.evidence.overall_status == FAIL


def test_evidence_property_reuses_ig080_overall_status_human_debt():
    receipt = build_receipt(_evidence((PASS, HUMAN_DEBT)))
    assert receipt.evidence.overall_status == HUMAN_DEBT
