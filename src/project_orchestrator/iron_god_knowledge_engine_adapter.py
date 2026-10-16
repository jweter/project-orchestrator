"""Knowledge Engine Product Reality adapter (roadmap section 6:
Product Reality Engine; Phase 8: Product Reality Framework).

Declares the Knowledge Engine's Product Reality evidence category
taxonomy from the roadmap -- representative real questions, usable
answers, retrieval functioning, no obvious hallucinated system
behavior, expected source/context use, latency within reasonable
bounds, graceful failure where information is unavailable -- none of
which the roadmap marks as inherently subjective/human-only for this
product (contrast with Everward, iron_god_everward_adapter.py,
IG-083, where visual/gameplay evaluation is explicitly named human
debt). This module is CONTRACT ONLY, satisfying the IG-080
ProductRealityAdapter Protocol: a real evaluation requires actually
running the Knowledge Engine (real retrieval, real latency
measurement), out of scope for this session. This module performs no
evaluation, file, or network operation itself.
"""

from __future__ import annotations

from .iron_god_product_reality_adapter import ProductRealityEvidence

REPRESENTATIVE_REAL_QUESTIONS = "representative_real_questions"
USABLE_ANSWERS = "usable_answers"
RETRIEVAL_FUNCTIONING = "retrieval_functioning"
NO_HALLUCINATED_BEHAVIOR = "no_hallucinated_behavior"
EXPECTED_SOURCE_CONTEXT_USE = "expected_source_context_use"
LATENCY_WITHIN_BOUNDS = "latency_within_bounds"
GRACEFUL_FAILURE_ON_MISSING_INFO = "graceful_failure_on_missing_info"

KNOWN_CATEGORIES = (
    REPRESENTATIVE_REAL_QUESTIONS,
    USABLE_ANSWERS,
    RETRIEVAL_FUNCTIONING,
    NO_HALLUCINATED_BEHAVIOR,
    EXPECTED_SOURCE_CONTEXT_USE,
    LATENCY_WITHIN_BOUNDS,
    GRACEFUL_FAILURE_ON_MISSING_INFO,
)

HUMAN_DEBT_CATEGORIES: frozenset[str] = frozenset()

_NOT_IMPLEMENTED = (
    "Knowledge Engine Product Reality evaluation for {repository!r} is not "
    "implemented in this session; a real adapter requires actually running "
    "the Knowledge Engine (real retrieval, real latency measurement), which "
    "is out of scope here."
)


class KnowledgeEngineAdapter:
    def evaluate(self, repository: str, candidate_sha: str) -> ProductRealityEvidence:
        raise NotImplementedError(_NOT_IMPLEMENTED.format(repository=repository))
