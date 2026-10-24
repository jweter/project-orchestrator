"""Deterministic evidence receipts (roadmap: the accepted-milestone
evidence set names a "Product Reality receipt" alongside the
Verification Receipt; Phase 8: Product Reality Framework, final
deliverable).

Wraps a ProductRealityEvidence
(iron_god_product_reality_adapter.py, IG-080) in an immutable,
tamper-detecting receipt, directly mirroring
iron_god_verification_receipt.py's (IG-030) content-hash pattern. A
receipt exposes an `evidence` property that reconstructs the
ProductRealityEvidence so `overall_status` reuses IG-080's own
PASS/FAIL/HUMAN_DEBT precedence logic rather than duplicating it.
This module performs no evaluation itself -- it only formalizes the
receipt shape.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .iron_god_product_reality_adapter import (
    EvidenceCategoryResult,
    ProductRealityEvidence,
)

REQUIRED_RECEIPT_FIELDS = (
    "repository",
    "candidate_sha",
    "category_results",
    "content_hash",
)
REQUIRED_CATEGORY_FIELDS = ("category", "status", "detail")


class EvidenceReceiptValidationError(RuntimeError):
    """Raised when a receipt fails validation; callers must treat the
    Product Reality evaluation as unproven, never as passing."""


@dataclass(frozen=True)
class ProductRealityReceipt:
    repository: str
    candidate_sha: str
    category_results: tuple[EvidenceCategoryResult, ...]
    content_hash: str

    @property
    def evidence(self) -> ProductRealityEvidence:
        return ProductRealityEvidence(
            repository=self.repository,
            candidate_sha=self.candidate_sha,
            category_results=self.category_results,
        )


def compute_content_hash(
    repository: str,
    candidate_sha: str,
    category_results: tuple[EvidenceCategoryResult, ...],
) -> str:
    """A stable hash over the receipt's own evidence, used to detect
    tampering after the fact."""
    payload = {
        "repository": repository,
        "candidate_sha": candidate_sha,
        "category_results": [
            {
                "category": result.category,
                "status": result.status,
                "detail": result.detail,
            }
            for result in sorted(category_results, key=lambda r: r.category)
        ],
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_receipt(evidence: ProductRealityEvidence) -> ProductRealityReceipt:
    """Construct a receipt with a freshly-computed, correct content hash."""
    content_hash = compute_content_hash(
        evidence.repository, evidence.candidate_sha, evidence.category_results
    )
    return ProductRealityReceipt(
        repository=evidence.repository,
        candidate_sha=evidence.candidate_sha,
        category_results=evidence.category_results,
        content_hash=content_hash,
    )


def validate_receipt(payload: dict) -> ProductRealityReceipt:
    """Parse and validate a raw (e.g. JSON-decoded) receipt payload.

    Fails closed: any missing field, malformed category entry,
    duplicate category name, or content-hash mismatch raises
    EvidenceReceiptValidationError rather than returning a
    best-effort receipt.
    """
    for field_name in REQUIRED_RECEIPT_FIELDS:
        if field_name not in payload:
            raise EvidenceReceiptValidationError(
                f"receipt is missing required field: {field_name!r}"
            )

    raw_results = payload["category_results"]
    if not isinstance(raw_results, list) or not raw_results:
        raise EvidenceReceiptValidationError(
            "receipt must have a non-empty list of category_results"
        )

    results: list[EvidenceCategoryResult] = []
    for raw in raw_results:
        if not isinstance(raw, dict):
            raise EvidenceReceiptValidationError(
                "each category entry must be an object"
            )
        for field_name in REQUIRED_CATEGORY_FIELDS:
            if field_name not in raw:
                raise EvidenceReceiptValidationError(
                    f"category entry is missing required field: {field_name!r}"
                )
        results.append(
            EvidenceCategoryResult(
                category=raw["category"], status=raw["status"], detail=raw["detail"]
            )
        )

    names = [result.category for result in results]
    if len(names) != len(set(names)):
        raise EvidenceReceiptValidationError("receipt contains duplicate categories")

    expected_hash = compute_content_hash(
        payload["repository"], payload["candidate_sha"], tuple(results)
    )
    if payload["content_hash"] != expected_hash:
        raise EvidenceReceiptValidationError(
            f"content_hash mismatch: recorded {payload['content_hash']!r} does not "
            f"match recomputed {expected_hash!r}; receipt may have been tampered with"
        )

    return ProductRealityReceipt(
        repository=payload["repository"],
        candidate_sha=payload["candidate_sha"],
        category_results=tuple(results),
        content_hash=payload["content_hash"],
    )
