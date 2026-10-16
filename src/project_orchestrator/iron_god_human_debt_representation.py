"""Human-only debt representation (roadmap section 31: Manual
Intervention Policy; Phase 8: Product Reality Framework).

"Jeremy should be required only for things that genuinely require
Jeremy" -- and "subjective Product Reality decision that cannot be
automated" is one of the roadmap's own named examples. Composes a
ProductRealityEvidence (iron_god_product_reality_adapter.py, IG-080)
with an adapter's declared HUMAN_DEBT_CATEGORIES (IG-081 through
IG-084) to extract a durable human-debt record for exactly those
categories, pending Jeremy's sign-off. If evidence reports any status
other than HUMAN_DEBT for a category the adapter's own taxonomy
declares subjective, that is a contradiction and raises rather than
being trusted -- a subjective decision must never be silently
resolved as PASS or FAIL by an automated system. This module never
resolves a human-debt item itself; it only surfaces it. It performs
no file or state mutation.
"""

from __future__ import annotations

from dataclasses import dataclass

from .iron_god_product_reality_adapter import HUMAN_DEBT, ProductRealityEvidence

PENDING_SIGN_OFF = "PENDING_SIGN_OFF"


class HumanDebtRepresentationError(RuntimeError):
    """Raised when evidence contradicts the declared human-debt taxonomy."""


@dataclass(frozen=True)
class HumanDebtItem:
    repository: str
    candidate_sha: str
    category: str
    automated_detail: str
    status: str


def extract_human_debt(
    evidence: ProductRealityEvidence,
    human_debt_categories: frozenset[str],
) -> tuple[HumanDebtItem, ...]:
    """Extract a HumanDebtItem for every category in `evidence` that
    the adapter's own taxonomy declares subjective.

    Raises HumanDebtRepresentationError if a declared-subjective
    category's evidence status is anything other than HUMAN_DEBT --
    an automated PASS or FAIL on a subjective category is never
    trusted.
    """
    items: list[HumanDebtItem] = []
    for result in evidence.category_results:
        if result.category not in human_debt_categories:
            continue
        if result.status != HUMAN_DEBT:
            raise HumanDebtRepresentationError(
                f"category {result.category!r} is declared human-debt but "
                f"evidence reports status {result.status!r} instead of "
                "HUMAN_DEBT; a subjective decision must never be silently "
                "resolved as PASS or FAIL"
            )
        items.append(
            HumanDebtItem(
                repository=evidence.repository,
                candidate_sha=evidence.candidate_sha,
                category=result.category,
                automated_detail=result.detail,
                status=PENDING_SIGN_OFF,
            )
        )
    return tuple(items)
