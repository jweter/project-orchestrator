"""Bounded, read-only Knowledge Engine Core Product Reality retrieval probe.

This is an independent Phase-8 evidence producer, NOT an acceptance authority.
It invokes the real local Core CLI, checks reviewed record IDs, provenance,
latency and a negative query, and leaves semantic answer quality/hallucination
review as HUMAN_DEBT. No Git refs, SQLite acceptance, or controller state move.
The caller must separately prove the exact candidate HEAD and binary identity.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any, Callable

from .iron_god_knowledge_engine_adapter import (
    EXPECTED_SOURCE_CONTEXT_USE,
    GRACEFUL_FAILURE_ON_MISSING_INFO,
    LATENCY_WITHIN_BOUNDS,
    NO_HALLUCINATED_BEHAVIOR,
    REPRESENTATIVE_REAL_QUESTIONS,
    RETRIEVAL_FUNCTIONING,
    USABLE_ANSWERS,
)
from .iron_god_product_reality_adapter import (
    FAIL,
    HUMAN_DEBT,
    PASS,
    EvidenceCategoryResult,
    ProductRealityEvidence,
)

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_MAX_OUTPUT = 2_000_000
_MAX_CASES = 16
_MAX_RECORD_IDS_PER_CASE = 32
_MAX_QUESTION_LENGTH = 2048
_MAX_RECORD_ID_LENGTH = 256


@dataclass(frozen=True)
class GoldenRetrievalCase:
    question: str
    expected_record_ids: tuple[str, ...]


def _valid_case(case: object) -> bool:
    """Bound the number and size of real CLI invocations and expected IDs."""
    return (
        isinstance(case, GoldenRetrievalCase)
        and isinstance(case.question, str)
        and 0 < len(case.question.strip()) <= _MAX_QUESTION_LENGTH
        and isinstance(case.expected_record_ids, tuple)
        and 0 < len(case.expected_record_ids) <= _MAX_RECORD_IDS_PER_CASE
        and all(
            isinstance(record_id, str)
            and 0 < len(record_id.strip()) <= _MAX_RECORD_ID_LENGTH
            for record_id in case.expected_record_ids
        )
    )


class KnowledgeEngineLiveRetrievalAdapter:
    """Run deterministic retrieval through the installed Core CLI, never a mock.

    A test may inject a runner; only the default subprocess runner constitutes
    actual runtime evidence. This adapter deliberately cannot certify semantic
    usability or hallucination absence from lexical retrieval alone.
    """

    def __init__(
        self,
        *,
        expected_repository: str,
        expected_candidate_sha: str,
        core_root: Path,
        python_executable: Path,
        ke_script: Path,
        sources: Path,
        evidence: Path,
        cases: tuple[GoldenRetrievalCase, ...],
        negative_question: str | None = None,
        max_latency_seconds: float = 30.0,
        timeout_seconds: float = 45.0,
        runner: Callable[..., Any] = subprocess.run,
    ) -> None:
        self.expected_repository = expected_repository
        self.expected_candidate_sha = expected_candidate_sha
        self.core_root = Path(core_root)
        self.python_executable = Path(python_executable)
        self.ke_script = Path(ke_script)
        self.sources = Path(sources)
        self.evidence = Path(evidence)
        self.cases = cases
        self.negative_question = negative_question
        self.max_latency_seconds = max_latency_seconds
        self.timeout_seconds = timeout_seconds
        self.runner = runner

    def _report(self, question: str) -> tuple[list[dict[str, Any]], float]:
        command = [
            str(self.python_executable),
            str(self.ke_script),
            "evidence-report",
            question,
            "--sources",
            str(self.sources),
            "--evidence",
            str(self.evidence),
            "--limit",
            "10",
            "--format",
            "json",
        ]
        start = monotonic()
        result = self.runner(
            command,
            cwd=str(self.core_root),
            capture_output=True,
            text=True,
            shell=False,
            check=False,
            timeout=self.timeout_seconds,
        )
        elapsed = monotonic() - start
        if result.returncode != 0 or not isinstance(result.stdout, str):
            raise ValueError("CORE_CLI_UNAVAILABLE_OR_FAILED")
        if len(result.stdout.encode("utf-8")) > _MAX_OUTPUT:
            raise ValueError("CORE_OUTPUT_TOO_LARGE")
        payload = json.loads(result.stdout)
        if (
            not isinstance(payload, dict)
            or type(payload.get("schema_version")) is not int
            or payload["schema_version"] != 1
        ):
            raise ValueError("CORE_SCHEMA_UNVERIFIED")
        if payload.get("question") != question or not isinstance(
            payload.get("papers"), list
        ):
            raise ValueError("CORE_RESPONSE_IDENTITY_UNVERIFIED")
        papers = payload["papers"]
        if any(not isinstance(paper, dict) for paper in papers):
            raise ValueError("CORE_PAPER_SCHEMA_UNVERIFIED")
        return papers, elapsed

    def evaluate(self, repository: str, candidate_sha: str) -> ProductRealityEvidence:
        statuses = {
            REPRESENTATIVE_REAL_QUESTIONS: (
                HUMAN_DEBT,
                "reviewed question provenance not independently certified",
            ),
            USABLE_ANSWERS: (
                HUMAN_DEBT,
                "semantic answer usability requires independent evaluation",
            ),
            RETRIEVAL_FUNCTIONING: (FAIL, "real retrieval not verified"),
            NO_HALLUCINATED_BEHAVIOR: (
                HUMAN_DEBT,
                "retrieval alone cannot certify generated-answer behavior",
            ),
            EXPECTED_SOURCE_CONTEXT_USE: (FAIL, "source provenance not verified"),
            LATENCY_WITHIN_BOUNDS: (FAIL, "real latency not verified"),
            GRACEFUL_FAILURE_ON_MISSING_INFO: (
                HUMAN_DEBT,
                "negative retrieval case not exercised",
            ),
        }

        def result() -> ProductRealityEvidence:
            return ProductRealityEvidence(
                repository=repository,
                candidate_sha=candidate_sha,
                category_results=tuple(
                    EvidenceCategoryResult(category=k, status=v[0], detail=v[1])
                    for k, v in statuses.items()
                ),
            )

        if (
            repository != self.expected_repository
            or not isinstance(candidate_sha, str)
            or not _SHA.fullmatch(candidate_sha)
            or candidate_sha != self.expected_candidate_sha
            or not isinstance(self.cases, tuple)
            or not (0 < len(self.cases) <= _MAX_CASES)
            or not all(_valid_case(case) for case in self.cases)
            or (
                self.negative_question is not None
                and (
                    not isinstance(self.negative_question, str)
                    or not 0
                    < len(self.negative_question.strip())
                    <= _MAX_QUESTION_LENGTH
                )
            )
            or not (0 < self.max_latency_seconds <= self.timeout_seconds <= 120)
            or not self.core_root.is_dir()
            or not all(
                path.is_file()
                for path in (
                    self.python_executable,
                    self.ke_script,
                    self.sources,
                    self.evidence,
                )
            )
        ):
            statuses[RETRIEVAL_FUNCTIONING] = (
                FAIL,
                "candidate identity or local runtime inputs unverified",
            )
            return result()

        try:
            retrieval_ok = True
            provenance_ok = True
            latency_ok = True
            for case in self.cases:
                papers, elapsed = self._report(case.question)
                latency_ok &= elapsed <= self.max_latency_seconds
                found: set[str] = set()
                cited: set[str] = set()
                for paper in papers:
                    citation = paper.get("citation")
                    source_ids = (paper.get("doi"), paper.get("source_url"))
                    has_provenance = (
                        isinstance(citation, str)
                        and bool(citation.strip())
                        and any(
                            isinstance(source_id, str) and bool(source_id.strip())
                            for source_id in source_ids
                        )
                    )
                    records = paper.get("evidence_records")
                    if not isinstance(records, list):
                        raise ValueError("CORE_EVIDENCE_RECORDS_UNVERIFIED")
                    for record in records:
                        if not isinstance(record, dict):
                            raise ValueError("CORE_EVIDENCE_RECORD_UNVERIFIED")
                        record_id = record.get("evidence_record_id")
                        if isinstance(record_id, str) and record_id:
                            found.add(record_id)
                            if has_provenance:
                                cited.add(record_id)
                expected = set(case.expected_record_ids)
                retrieval_ok &= expected.issubset(found)
                provenance_ok &= expected.issubset(cited)
            statuses[RETRIEVAL_FUNCTIONING] = (
                PASS if retrieval_ok else FAIL,
                "all pinned evidence IDs retrieved"
                if retrieval_ok
                else "expected evidence IDs missing",
            )
            statuses[EXPECTED_SOURCE_CONTEXT_USE] = (
                PASS if provenance_ok else FAIL,
                "matched records carry source citations"
                if provenance_ok
                else "source citation missing",
            )
            if self.negative_question:
                negative_papers, elapsed = self._report(self.negative_question)
                latency_ok &= elapsed <= self.max_latency_seconds
                statuses[GRACEFUL_FAILURE_ON_MISSING_INFO] = (
                    PASS if not negative_papers else FAIL,
                    "unanswerable query returned no papers"
                    if not negative_papers
                    else "unanswerable query returned matches",
                )
            statuses[LATENCY_WITHIN_BOUNDS] = (
                PASS if latency_ok else FAIL,
                "measured within configured wall-clock bound"
                if latency_ok
                else "latency exceeded bound",
            )
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            subprocess.TimeoutExpired,
            TypeError,
        ):
            statuses[RETRIEVAL_FUNCTIONING] = (
                FAIL,
                "Core runtime execution or output validation failed",
            )
            statuses[EXPECTED_SOURCE_CONTEXT_USE] = (
                FAIL,
                "source provenance unverified",
            )
            statuses[LATENCY_WITHIN_BOUNDS] = (
                FAIL,
                "bounded runtime measurement incomplete",
            )
        return result()
