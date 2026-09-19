"""Evidence-first external capability acquisition for Daedalus."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

class Disposition(str, Enum):
    INTEGRATE = "INTEGRATE"
    ADAPT = "ADAPT"
    BENCHMARK = "BENCHMARK"
    REJECT = "REJECT"

@dataclass(frozen=True)
class ExternalCapability:
    name: str
    repository: str
    license: str
    capabilities: tuple[str, ...]
    target_products: tuple[str, ...]
    priority: int
    dependency_risk: int = 1
    evidence_url: str = ""

    def score(self) -> int:
        return self.priority * 3 + len(self.target_products) + len(self.capabilities) - self.dependency_risk

def classify(candidate: ExternalCapability, existing: Iterable[str]) -> Disposition:
    known = set(existing)
    overlap = len(known.intersection(candidate.capabilities))
    if candidate.license.upper() not in {"MIT", "APACHE-2.0", "BSD-3-CLAUSE"}:
        return Disposition.BENCHMARK
    if not candidate.capabilities:
        return Disposition.REJECT
    if overlap >= max(1, len(candidate.capabilities) // 2):
        return Disposition.ADAPT
    if candidate.dependency_risk <= 2:
        return Disposition.INTEGRATE
    return Disposition.BENCHMARK

def rank(candidates: Iterable[ExternalCapability]) -> list[ExternalCapability]:
    return sorted(candidates, key=lambda item: (-item.score(), item.name.lower()))

def evaluation_record(candidate: ExternalCapability, existing: Iterable[str]) -> dict[str, object]:
    return {
        "name": candidate.name,
        "repository": candidate.repository,
        "license": candidate.license,
        "capabilities": list(candidate.capabilities),
        "target_products": list(candidate.target_products),
        "score": candidate.score(),
        "disposition": classify(candidate, existing).value,
        "evidence_url": candidate.evidence_url,
        "status": "EVALUATION_REQUIRED",
    }
