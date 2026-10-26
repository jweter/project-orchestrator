from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from project_orchestrator.iron_god_candidate_local_acceptance import (
    ACCEPTED,
    NOT_ACCEPTED,
    CandidateAcceptanceOutcome,
    accept_verified_candidate_as_next_milestone,
)
from project_orchestrator.iron_god_milestone_resolution import (
    resolve_latest_committed_milestone,
)
from project_orchestrator.iron_god_product_reality_adapter import (
    FAIL,
    HUMAN_DEBT,
    PASS,
    EvidenceCategoryResult,
    ProductRealityEvidence,
)
from project_orchestrator.local_first_state import LocalFirstState
from project_orchestrator.local_git_refs import LocalGitRefs
from tests.owner_authority import grant_owner_authority, revoke_owner_authority

REPO_IDENTIFIER = "test-proof-owner/test-proof-repo"


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "proof@example.com")
    _git(path, "config", "user.name", "Proof")
    (path / "README.md").write_text("proof repo\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "init")
    return path


class _FixedAdapter:
    def __init__(self, evidence: ProductRealityEvidence) -> None:
        self._evidence = evidence
        self.calls: list[tuple[str, str]] = []

    def evaluate(self, repository: str, candidate_sha: str) -> ProductRealityEvidence:
        self.calls.append((repository, candidate_sha))
        return self._evidence


@pytest.fixture()
def env(tmp_path):
    flow = tmp_path / "flow-state"
    flow.mkdir()
    grant_owner_authority(flow)

    repo = _init_repo(tmp_path / "repo")
    base_sha = _git(repo, "rev-parse", "HEAD")

    refs = LocalGitRefs(repo, owner_state_root=flow)
    refs.bootstrap_accepted(base_sha)
    refs.protect("milestone", "M-0", base_sha)

    state = LocalFirstState(tmp_path / "accepted.sqlite")
    state.prepare_acceptance(
        repository=REPO_IDENTIFIER,
        milestone_id="M-0",
        accepted_base_sha="0" * 40,
        accepted_sha=base_sha,
        verification_sha256="a" * 64,
        product_reality="NOT_REQUIRED",
        context_json="{}",
    )
    state.finish_prepared_acceptance(
        milestone_id="M-0", expected_sha=base_sha, expected_verification_sha256="a" * 64
    )

    # A real candidate commit descending from base_sha -- what a
    # VERIFIED_CANDIDATE's own candidate_sha would look like.
    (repo / "feature.txt").write_text("feature\n", encoding="utf-8")
    _git(repo, "add", "feature.txt")
    _git(repo, "commit", "-q", "-m", "candidate commit")
    candidate_sha = _git(repo, "rev-parse", "HEAD")

    return {
        "repo": repo,
        "flow": flow,
        "base_sha": base_sha,
        "candidate_sha": candidate_sha,
        "refs": refs,
        "state": state,
    }


def test_not_required_accepts_and_advances_real_refs(env):
    outcome = accept_verified_candidate_as_next_milestone(
        state=env["state"],
        refs=env["refs"],
        repository=REPO_IDENTIFIER,
        base_sha=env["base_sha"],
        candidate_sha=env["candidate_sha"],
        new_milestone_id="M-1",
        verification_sha256="b" * 64,
        product_reality_required=False,
    )
    assert outcome.status == ACCEPTED
    assert outcome.milestone_id == "M-1"
    assert outcome.accepted_sha == env["candidate_sha"]
    assert outcome.product_reality.status == "NOT_REQUIRED"

    assert env["refs"].accepted() == env["candidate_sha"]
    assert env["refs"].get("milestone", "M-1") == env["candidate_sha"]

    latest = resolve_latest_committed_milestone(
        env["state"].snapshot(), REPO_IDENTIFIER
    )
    assert latest.milestone_id == "M-1"
    assert latest.accepted_sha == env["candidate_sha"]


def test_required_pass_accepts(env):
    adapter = _FixedAdapter(
        ProductRealityEvidence(
            repository=REPO_IDENTIFIER,
            candidate_sha=env["candidate_sha"],
            category_results=(EvidenceCategoryResult("x", PASS, "ok"),),
        )
    )
    outcome = accept_verified_candidate_as_next_milestone(
        state=env["state"],
        refs=env["refs"],
        repository=REPO_IDENTIFIER,
        base_sha=env["base_sha"],
        candidate_sha=env["candidate_sha"],
        new_milestone_id="M-1",
        verification_sha256="b" * 64,
        product_reality_adapter=adapter,
        product_reality_required=True,
    )
    assert outcome.status == ACCEPTED
    assert adapter.calls == [(REPO_IDENTIFIER, env["candidate_sha"])]
    assert env["refs"].accepted() == env["candidate_sha"]


def test_required_fail_blocks_and_advances_nothing(env):
    adapter = _FixedAdapter(
        ProductRealityEvidence(
            repository=REPO_IDENTIFIER,
            candidate_sha=env["candidate_sha"],
            category_results=(EvidenceCategoryResult("gameplay", FAIL, "crashes"),),
        )
    )
    outcome = accept_verified_candidate_as_next_milestone(
        state=env["state"],
        refs=env["refs"],
        repository=REPO_IDENTIFIER,
        base_sha=env["base_sha"],
        candidate_sha=env["candidate_sha"],
        new_milestone_id="M-1",
        verification_sha256="b" * 64,
        product_reality_adapter=adapter,
        product_reality_required=True,
    )
    assert outcome.status == NOT_ACCEPTED
    assert outcome.milestone_id is None
    assert outcome.accepted_sha is None

    # Nothing advanced: accepted ref unchanged, no new milestone ref, no
    # new SQLite acceptance row.
    assert env["refs"].accepted() == env["base_sha"]
    assert env["refs"].get("milestone", "M-1") is None
    latest = resolve_latest_committed_milestone(
        env["state"].snapshot(), REPO_IDENTIFIER
    )
    assert latest.milestone_id == "M-0"


def test_required_human_debt_blocks_and_advances_nothing(env):
    adapter = _FixedAdapter(
        ProductRealityEvidence(
            repository=REPO_IDENTIFIER,
            candidate_sha=env["candidate_sha"],
            category_results=(
                EvidenceCategoryResult("visual_quality", HUMAN_DEBT, "needs review"),
            ),
        )
    )
    outcome = accept_verified_candidate_as_next_milestone(
        state=env["state"],
        refs=env["refs"],
        repository=REPO_IDENTIFIER,
        base_sha=env["base_sha"],
        candidate_sha=env["candidate_sha"],
        new_milestone_id="M-1",
        verification_sha256="b" * 64,
        product_reality_adapter=adapter,
        product_reality_required=True,
    )
    assert outcome.status == NOT_ACCEPTED
    assert env["refs"].accepted() == env["base_sha"]


def test_required_with_no_adapter_raises_and_advances_nothing(env):
    with pytest.raises(ValueError):
        accept_verified_candidate_as_next_milestone(
            state=env["state"],
            refs=env["refs"],
            repository=REPO_IDENTIFIER,
            base_sha=env["base_sha"],
            candidate_sha=env["candidate_sha"],
            new_milestone_id="M-1",
            verification_sha256="b" * 64,
            product_reality_required=True,
        )
    assert env["refs"].accepted() == env["base_sha"]


def test_outcome_is_frozen_dataclass():
    with pytest.raises(AttributeError):
        CandidateAcceptanceOutcome(
            status=NOT_ACCEPTED,
            milestone_id=None,
            accepted_sha=None,
            product_reality=None,
            reason="x",
        ).status = "mutated"
