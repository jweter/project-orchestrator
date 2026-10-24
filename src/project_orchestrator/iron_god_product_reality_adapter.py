"""Product Reality adapter interface (roadmap section 6: Product
Reality Engine; Phase 8: Product Reality Framework, first
deliverable).

"Iron God must become good at producing software, not merely green
test suites... Passing tests alone is insufficient for local
acceptance when Product Reality is required." Defines the
per-repository Product Reality adapter Protocol every repository
receives, plus the evidence vocabulary every real adapter (Knowledge
Engine, Rocksmith, Everward, VoiceStudio) reports against. A
HUMAN_DEBT category is a subjective evaluation that cannot be
automated -- it is never silently treated as passing. This module is
CONTRACT ONLY: a real evaluation requires the actual domain runtime
(Unreal Engine, an audio DSP pipeline, a real Rocksmith CDLC
generator, etc.), out of scope for this session, matching the
IG-042/IG-055 contract-only precedent. This module performs no
evaluation, file, or network operation itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

PASS = "PASS"
FAIL = "FAIL"
HUMAN_DEBT = "HUMAN_DEBT"

_NOT_IMPLEMENTED = (
    "Product Reality evaluation for {repository!r} is not implemented in "
    "this session; a real adapter requires the actual domain runtime (e.g. "
    "Unreal Engine, an audio DSP pipeline, a real CDLC generator), which is "
    "out of scope here."
)


@dataclass(frozen=True)
class EvidenceCategoryResult:
    category: str
    status: str
    detail: str


@dataclass(frozen=True)
class ProductRealityEvidence:
    repository: str
    candidate_sha: str
    category_results: tuple[EvidenceCategoryResult, ...]

    @property
    def overall_status(self) -> str:
        """FAIL if any category is FAIL. Otherwise HUMAN_DEBT if any
        category is HUMAN_DEBT -- a subjective category is never
        silently treated as passing. Otherwise PASS only when every
        category explicitly PASSed; empty or otherwise inconclusive
        evidence never defaults to PASS."""
        statuses = {result.status for result in self.category_results}
        if FAIL in statuses:
            return FAIL
        if HUMAN_DEBT in statuses:
            return HUMAN_DEBT
        if statuses == {PASS}:
            return PASS
        return FAIL


@runtime_checkable
class ProductRealityAdapter(Protocol):
    def evaluate(
        self, repository: str, candidate_sha: str
    ) -> ProductRealityEvidence: ...


class NotImplementedAdapter:
    def evaluate(self, repository: str, candidate_sha: str) -> ProductRealityEvidence:
        raise NotImplementedError(_NOT_IMPLEMENTED.format(repository=repository))
