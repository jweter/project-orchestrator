"""Quantitative context-compression benchmark for agent evidence payloads."""
from __future__ import annotations
from dataclasses import dataclass
from time import perf_counter
from typing import Callable, Iterable
import re

_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)

@dataclass(frozen=True)
class EvidenceCase:
    name: str
    payload: str
    essential_markers: tuple[str, ...]

@dataclass(frozen=True)
class BenchmarkResult:
    name: str
    raw_tokens: int
    compressed_tokens: int
    compression_ratio: float
    markers_total: int
    markers_retained: int
    signal_retention: float
    latency_ms: float

def estimated_tokens(text: str) -> int:
    """Deterministic tokenizer-independent estimate; report as estimated, never provider-exact."""
    return len(_TOKEN_RE.findall(text))

def benchmark_case(case: EvidenceCase, compressor: Callable[[str], str]) -> BenchmarkResult:
    started=perf_counter()
    compressed=compressor(case.payload)
    latency_ms=(perf_counter()-started)*1000.0
    if not isinstance(compressed,str): raise TypeError("compressor must return text")
    raw=estimated_tokens(case.payload); after=estimated_tokens(compressed)
    retained=sum(marker in compressed for marker in case.essential_markers)
    total=len(case.essential_markers)
    return BenchmarkResult(
        name=case.name,
        raw_tokens=raw,
        compressed_tokens=after,
        compression_ratio=round((after/raw) if raw else 1.0,4),
        markers_total=total,
        markers_retained=retained,
        signal_retention=round((retained/total) if total else 1.0,4),
        latency_ms=round(latency_ms,3),
    )

def benchmark_suite(cases: Iterable[EvidenceCase], compressor: Callable[[str],str]) -> dict[str,object]:
    results=[benchmark_case(case,compressor) for case in cases]
    raw=sum(x.raw_tokens for x in results); after=sum(x.compressed_tokens for x in results)
    markers=sum(x.markers_total for x in results); retained=sum(x.markers_retained for x in results)
    return {
        "token_metric":"DETERMINISTIC_ESTIMATE_NOT_PROVIDER_TOKENIZER",
        "cases":[x.__dict__ for x in results],
        "raw_tokens":raw,
        "compressed_tokens":after,
        "compression_ratio":round((after/raw) if raw else 1.0,4),
        "signal_retention":round((retained/markers) if markers else 1.0,4),
        "latency_ms":round(sum(x.latency_ms for x in results),3),
    }

def promotion_decision(report: dict[str,object], *, max_ratio: float=0.70, min_signal_retention: float=1.0) -> str:
    ratio=float(report["compression_ratio"]); signal=float(report["signal_retention"])
    if signal < min_signal_retention: return "REJECT_SIGNAL_LOSS"
    if ratio <= max_ratio: return "PILOT_READY"
    return "BENCHMARK_ONLY"
