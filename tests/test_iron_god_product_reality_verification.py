from __future__ import annotations

import pytest

from project_orchestrator.iron_god_product_reality_adapter import (
    EvidenceCategoryResult,
    ProductRealityEvidence,
)
from project_orchestrator.iron_god_product_reality_verification import (
    NOT_REQUIRED,
    ProductRealityGateDecision,
    evaluate_product_reality_for_candidate,
)
from project_orchestrator.iron_god_product_reality_adapter import FAIL, HUMAN_DEBT, PASS

REPO = "demo-owner/demo-repo"
SHA = "c" * 40


class _FakeAdapter:
    def __init__(self, evidence: ProductRealityEvidence) -> None:
        self._evidence = evidence
        self.calls: list[tuple[str, str]] = []

    def evaluate(self, repository: str, candidate_sha: str) -> ProductRealityEvidence:
        self.calls.append((repository, candidate_sha))
        return self._evidence


def test_not_required_never_calls_adapter():
    adapter = _FakeAdapter(
        ProductRealityEvidence(
            repository=REPO,
            candidate_sha=SHA,
            category_results=(EvidenceCategoryResult("x", PASS, "ok"),),
        )
    )
    decision = evaluate_product_reality_for_candidate(
        adapter, REPO, SHA, required=False
    )
    assert decision.status == NOT_REQUIRED
    assert decision.blocks_acceptance is False
    assert decision.receipt is None
    assert adapter.calls == []


def test_required_with_no_adapter_raises():
    with pytest.raises(ValueError):
        evaluate_product_reality_for_candidate(None, REPO, SHA, required=True)


def test_required_all_pass_does_not_block():
    adapter = _FakeAdapter(
        ProductRealityEvidence(
            repository=REPO,
            candidate_sha=SHA,
            category_results=(
                EvidenceCategoryResult("gameplay", PASS, "all scenarios pass"),
                EvidenceCategoryResult("audio", PASS, "no clipping detected"),
            ),
        )
    )
    decision = evaluate_product_reality_for_candidate(adapter, REPO, SHA, required=True)
    assert decision.status == PASS
    assert decision.blocks_acceptance is False
    assert decision.receipt is not None
    assert decision.receipt.repository == REPO
    assert decision.receipt.candidate_sha == SHA
    assert adapter.calls == [(REPO, SHA)]


def test_required_any_fail_blocks_acceptance():
    adapter = _FakeAdapter(
        ProductRealityEvidence(
            repository=REPO,
            candidate_sha=SHA,
            category_results=(
                EvidenceCategoryResult("gameplay", PASS, "ok"),
                EvidenceCategoryResult("audio", FAIL, "clipping detected at 2:14"),
            ),
        )
    )
    decision = evaluate_product_reality_for_candidate(adapter, REPO, SHA, required=True)
    assert decision.status == FAIL
    assert decision.blocks_acceptance is True
    assert decision.receipt is not None


def test_required_human_debt_blocks_acceptance_even_with_no_fail():
    adapter = _FakeAdapter(
        ProductRealityEvidence(
            repository=REPO,
            candidate_sha=SHA,
            category_results=(
                EvidenceCategoryResult("gameplay", PASS, "ok"),
                EvidenceCategoryResult(
                    "visual_quality", HUMAN_DEBT, "requires subjective review"
                ),
            ),
        )
    )
    decision = evaluate_product_reality_for_candidate(adapter, REPO, SHA, required=True)
    assert decision.status == HUMAN_DEBT
    assert decision.blocks_acceptance is True
    assert "human sign-off" in decision.reason
    # The subjective category must never be silently treated as passing.
    assert decision.receipt.evidence.overall_status == HUMAN_DEBT


def test_receipt_content_hash_is_real_and_tamper_detecting():
    adapter = _FakeAdapter(
        ProductRealityEvidence(
            repository=REPO,
            candidate_sha=SHA,
            category_results=(EvidenceCategoryResult("x", PASS, "ok"),),
        )
    )
    decision = evaluate_product_reality_for_candidate(adapter, REPO, SHA, required=True)
    receipt = decision.receipt

    from project_orchestrator.iron_god_product_reality_receipt import (
        EvidenceReceiptValidationError,
        compute_content_hash,
        validate_receipt,
    )

    assert receipt.content_hash == compute_content_hash(
        receipt.repository, receipt.candidate_sha, receipt.category_results
    )

    payload = {
        "repository": receipt.repository,
        "candidate_sha": receipt.candidate_sha,
        "category_results": [
            {"category": r.category, "status": r.status, "detail": r.detail}
            for r in receipt.category_results
        ],
        "content_hash": "tampered" + receipt.content_hash[8:],
    }
    with pytest.raises(EvidenceReceiptValidationError):
        validate_receipt(payload)


def test_decision_is_frozen_dataclass():
    decision = ProductRealityGateDecision(
        status=NOT_REQUIRED, blocks_acceptance=False, receipt=None, reason="x"
    )
    with pytest.raises(AttributeError):
        decision.status = "mutated"
