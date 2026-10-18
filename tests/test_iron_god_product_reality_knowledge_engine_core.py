"""Proves the real Knowledge Engine Core hybrid-search Product Reality
adapter against a disposable fake candidate worktree, before it is
ever pointed at the real knowledge-engine-core repo.

Two fixtures: a correct implementation of the pinned
merge_lexical_and_vector_results contract (must PASS), and an
incorrect one that silently drops a paper (must FAIL) -- proving this
adapter can genuinely fail, not just always report PASS.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from project_orchestrator.iron_god_product_reality_adapter import FAIL, PASS
from project_orchestrator.iron_god_product_reality_knowledge_engine_core import (
    KnowledgeEngineCoreHybridSearchAdapter,
)

REPOSITORY = "jweter/knowledge-engine-core"

_SEARCH_MODULE_SOURCE = """
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class SearchResult:
    paper_id: int
    title: str
    abstract: str | None
    publication_year: int | None
    doi: str | None
    score: float
    snippet: str
    matched_query: str
    evidence_alignment_score: float = 0.0
"""

_CORRECT_HYBRID_SEARCH_SOURCE = """
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class HybridSearchResult:
    paper_id: int
    combined_score: float
    lexical_score: float | None
    vector_score: float | None

def _normalize(values: dict[int, float]) -> dict[int, float]:
    if not values:
        return {}
    lo = min(values.values())
    hi = max(values.values())
    if hi == lo:
        return {k: 1.0 for k in values}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}

def merge_lexical_and_vector_results(lexical, vector_scores):
    lexical_raw = {r.paper_id: r.score for r in lexical}
    lexical_norm = _normalize(lexical_raw)
    vector_norm = _normalize(dict(vector_scores))
    all_ids = sorted(set(lexical_norm) | set(vector_norm))
    results = []
    for pid in all_ids:
        l = lexical_norm.get(pid)
        v = vector_norm.get(pid)
        combined = (l or 0.0) + (v or 0.0)
        results.append(HybridSearchResult(pid, combined, l, v))
    results.sort(key=lambda r: (-r.combined_score, r.paper_id))
    return results
"""

_BROKEN_HYBRID_SEARCH_SOURCE = """
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class HybridSearchResult:
    paper_id: int
    combined_score: float
    lexical_score: float | None
    vector_score: float | None

def merge_lexical_and_vector_results(lexical, vector_scores):
    # Bug: silently drops any paper not present in BOTH signals.
    lexical_ids = {r.paper_id: r.score for r in lexical}
    shared = set(lexical_ids) & set(vector_scores)
    return [
        HybridSearchResult(pid, lexical_ids[pid] + vector_scores[pid], lexical_ids[pid], vector_scores[pid])
        for pid in sorted(shared)
    ]
"""


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )


def _build_fake_candidate_worktree(
    tmp_path: Path, hybrid_search_source: str
) -> tuple[Path, str]:
    repo = tmp_path / "fake-ke-core"
    (repo / "knowledge_engine").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "proof@example.com")
    _git(repo, "config", "user.name", "Proof")
    (repo / "knowledge_engine" / "search.py").write_text(
        _SEARCH_MODULE_SOURCE, encoding="utf-8"
    )
    (repo / "knowledge_engine" / "hybrid_search.py").write_text(
        hybrid_search_source, encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "candidate")
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return repo, head


def test_correct_implementation_passes(tmp_path):
    repo, head = _build_fake_candidate_worktree(tmp_path, _CORRECT_HYBRID_SEARCH_SOURCE)
    adapter = KnowledgeEngineCoreHybridSearchAdapter(repo)
    evidence = adapter.evaluate(REPOSITORY, head)
    assert evidence.overall_status == PASS
    assert len(evidence.category_results) == 1
    assert evidence.category_results[0].category == "hybrid_search_ranking"
    assert evidence.category_results[0].status == PASS


def test_broken_implementation_fails_missing_single_signal_papers(tmp_path):
    repo, head = _build_fake_candidate_worktree(tmp_path, _BROKEN_HYBRID_SEARCH_SOURCE)
    adapter = KnowledgeEngineCoreHybridSearchAdapter(repo)
    evidence = adapter.evaluate(REPOSITORY, head)
    assert evidence.overall_status == FAIL
    assert "dropped" in evidence.category_results[0].detail


def test_mismatched_candidate_sha_fails_without_evaluating(tmp_path):
    repo, head = _build_fake_candidate_worktree(tmp_path, _CORRECT_HYBRID_SEARCH_SOURCE)
    adapter = KnowledgeEngineCoreHybridSearchAdapter(repo)
    evidence = adapter.evaluate(REPOSITORY, "0" * 40)
    assert evidence.overall_status == FAIL
    assert "does not match" in evidence.category_results[0].detail


def test_missing_module_fails_cleanly_not_raises(tmp_path):
    repo = tmp_path / "fake-ke-core-no-module"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "proof@example.com")
    _git(repo, "config", "user.name", "Proof")
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    adapter = KnowledgeEngineCoreHybridSearchAdapter(repo)
    evidence = adapter.evaluate(REPOSITORY, head)
    assert evidence.overall_status == FAIL
