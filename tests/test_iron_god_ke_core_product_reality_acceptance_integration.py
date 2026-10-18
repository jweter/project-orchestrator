"""Proves the real Knowledge Engine Core Product Reality adapter is
actually wired into the real local-acceptance path, per the owner's
2026-10-18 directive 2C: "Connect the Knowledge Engine Product
Reality adapter to the orchestration acceptance path. Prove that
actual candidate behavior determines acceptance eligibility. A
failing Product Reality result must prevent acceptance."

Scope: this tests accept_verified_candidate_as_next_milestone (the
real local-acceptance composition, Phase 5) using
KnowledgeEngineCoreHybridSearchAdapter (the real, just-built KE-core
adapter) against a real disposable Git repository whose candidate
commit contains either a correct or a deliberately broken
knowledge_engine/hybrid_search.py.

This deliberately does NOT go through the dispatch/claim validator
(accepted_event_dispatch_coordinator._require_exact_handoff_task),
which is a separate concern blocked on a real, confirmed path-
convention mismatch for Knowledge Engine Core
(KE_CORE_HANDOFF_PATH_CONVENTION_BLOCKER) -- that blocker is about
dispatching a real build to this repository, not about whether
Product Reality correctly gates acceptance once a candidate already
exists. Proving the acceptance path does not require dispatch.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from project_orchestrator.iron_god_candidate_local_acceptance import (
    ACCEPTED,
    NOT_ACCEPTED,
    accept_verified_candidate_as_next_milestone,
)
from project_orchestrator.iron_god_product_reality_knowledge_engine_core import (
    KnowledgeEngineCoreHybridSearchAdapter,
)
from project_orchestrator.local_first_state import LocalFirstState
from project_orchestrator.local_git_refs import LocalGitRefs

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

def _normalize(values):
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
    lexical_ids = {r.paper_id: r.score for r in lexical}
    shared = set(lexical_ids) & set(vector_scores)
    return [
        HybridSearchResult(pid, lexical_ids[pid] + vector_scores[pid], lexical_ids[pid], vector_scores[pid])
        for pid in sorted(shared)
    ]
"""


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _candidate_repo_with_hybrid_search(
    tmp_path: Path, hybrid_search_source: str
) -> tuple[Path, str, str]:
    """A real disposable repo with a base commit, then a candidate commit
    adding knowledge_engine/hybrid_search.py on top of it -- matching the
    real KE-core layout the adapter expects (package at repo root, no
    src/ prefix)."""
    repo = tmp_path / "candidate-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "proof@example.com")
    _git(repo, "config", "user.name", "Proof")
    (repo / "README.md").write_text("ke-core candidate proof\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "base")
    base_sha = _git(repo, "rev-parse", "HEAD")

    (repo / "knowledge_engine").mkdir()
    (repo / "knowledge_engine" / "search.py").write_text(
        _SEARCH_MODULE_SOURCE, encoding="utf-8"
    )
    (repo / "knowledge_engine" / "hybrid_search.py").write_text(
        hybrid_search_source, encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "candidate: hybrid search merge")
    candidate_sha = _git(repo, "rev-parse", "HEAD")
    return repo, base_sha, candidate_sha


def _seed_milestone0(tmp_path: Path, repo: Path, base_sha: str):
    flow = tmp_path / "flow-state"
    flow.mkdir()
    (flow / "human-mode.json").write_text('{"enabled": false}', encoding="utf-8")
    refs = LocalGitRefs(repo, owner_state_root=flow)
    refs.bootstrap_accepted(base_sha)
    refs.protect("milestone", "KE-CORE-PROOF-M0", base_sha)
    state = LocalFirstState(tmp_path / "accepted.sqlite")
    state.prepare_acceptance(
        repository=REPOSITORY,
        milestone_id="KE-CORE-PROOF-M0",
        accepted_base_sha="0" * 40,
        accepted_sha=base_sha,
        verification_sha256="a" * 64,
        product_reality="NOT_REQUIRED",
        context_json="{}",
    )
    state.finish_prepared_acceptance(
        milestone_id="KE-CORE-PROOF-M0",
        expected_sha=base_sha,
        expected_verification_sha256="a" * 64,
    )
    return state, refs


def test_correct_candidate_behavior_is_accepted(tmp_path):
    repo, base_sha, candidate_sha = _candidate_repo_with_hybrid_search(
        tmp_path, _CORRECT_HYBRID_SEARCH_SOURCE
    )
    state, refs = _seed_milestone0(tmp_path, repo, base_sha)
    adapter = KnowledgeEngineCoreHybridSearchAdapter(repo)

    outcome = accept_verified_candidate_as_next_milestone(
        state=state,
        refs=refs,
        repository=REPOSITORY,
        base_sha=base_sha,
        candidate_sha=candidate_sha,
        new_milestone_id="KE-CORE-PROOF-M1",
        verification_sha256="b" * 64,
        product_reality_adapter=adapter,
        product_reality_required=True,
    )

    assert outcome.status == ACCEPTED
    assert outcome.product_reality.status == "PASS"
    assert refs.accepted() == candidate_sha


def test_failing_candidate_behavior_prevents_acceptance(tmp_path):
    repo, base_sha, candidate_sha = _candidate_repo_with_hybrid_search(
        tmp_path, _BROKEN_HYBRID_SEARCH_SOURCE
    )
    state, refs = _seed_milestone0(tmp_path, repo, base_sha)
    adapter = KnowledgeEngineCoreHybridSearchAdapter(repo)

    outcome = accept_verified_candidate_as_next_milestone(
        state=state,
        refs=refs,
        repository=REPOSITORY,
        base_sha=base_sha,
        candidate_sha=candidate_sha,
        new_milestone_id="KE-CORE-PROOF-M1",
        verification_sha256="b" * 64,
        product_reality_adapter=adapter,
        product_reality_required=True,
    )

    assert outcome.status == NOT_ACCEPTED
    assert outcome.product_reality.status == "FAIL"
    assert "dropped" in outcome.product_reality.receipt.category_results[0].detail
    # Nothing advanced: a failing Product Reality result must leave the
    # accepted baseline exactly where it was, not partially advanced.
    assert refs.accepted() == base_sha
    assert refs.get("milestone", "KE-CORE-PROOF-M1") is None


def test_real_code_difference_is_what_changes_the_outcome(tmp_path):
    """The SAME acceptance call, the SAME adapter, the SAME milestone --
    only the candidate commit's actual code differs. This is the direct
    proof that real candidate behavior, not test wiring, determines
    acceptance eligibility."""
    (tmp_path / "good").mkdir()
    (tmp_path / "bad").mkdir()
    repo_good, base_good, candidate_good = _candidate_repo_with_hybrid_search(
        tmp_path / "good", _CORRECT_HYBRID_SEARCH_SOURCE
    )
    repo_bad, base_bad, candidate_bad = _candidate_repo_with_hybrid_search(
        tmp_path / "bad", _BROKEN_HYBRID_SEARCH_SOURCE
    )

    state_good, refs_good = _seed_milestone0(tmp_path / "good", repo_good, base_good)
    state_bad, refs_bad = _seed_milestone0(tmp_path / "bad", repo_bad, base_bad)

    outcome_good = accept_verified_candidate_as_next_milestone(
        state=state_good,
        refs=refs_good,
        repository=REPOSITORY,
        base_sha=base_good,
        candidate_sha=candidate_good,
        new_milestone_id="KE-CORE-PROOF-M1",
        verification_sha256="c" * 64,
        product_reality_adapter=KnowledgeEngineCoreHybridSearchAdapter(repo_good),
        product_reality_required=True,
    )
    outcome_bad = accept_verified_candidate_as_next_milestone(
        state=state_bad,
        refs=refs_bad,
        repository=REPOSITORY,
        base_sha=base_bad,
        candidate_sha=candidate_bad,
        new_milestone_id="KE-CORE-PROOF-M1",
        verification_sha256="c" * 64,
        product_reality_adapter=KnowledgeEngineCoreHybridSearchAdapter(repo_bad),
        product_reality_required=True,
    )

    assert outcome_good.status == ACCEPTED
    assert outcome_bad.status == NOT_ACCEPTED
