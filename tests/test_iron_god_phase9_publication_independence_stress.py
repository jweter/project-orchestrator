"""Phase 9 (Publication Independence Stress Tests) -- roadmap's 14
adversity scenarios, exit criteria: independent development
continues whenever safety permits; no verified commit is lost; no
duplicate execution occurs; no safety gate is bypassed.

Phase 9 has no IG-0xx numbered deliverables -- it is regression
protection composing already-built primitives, not new product code.

Coverage already proven elsewhere, not rebuilt here:
* scenario 6 (local worker crashes) and 11 (stale runtime lease):
  iron_god_stalled_worker_recovery.py's own test suite (IG-075).
* scenario 14 (one repo blocked, three remain actionable):
  iron_god_repository_fairness.py's own
  test_one_repo_blocked_three_remain_actionable_not_starved (IG-072).
* no duplicate execution: iron_god_publication_lane.py's
  MultipleActivePublicationsError test (IG-044) and
  iron_god_duplicate_suppression.py's own tests (IG-067).

New cross-module scenarios proven here:
* Scenario A (roadmap scenarios 1-5: GitHub unavailable, PR denied,
  CI delayed, CI failure, review delayed): a publication job's
  transport failure resolves to PUBLICATION_DEFERRED; the publication
  lane then reports no active publication; a worker occupying the
  publication lane does not reduce available development capacity;
  and -- critically -- a second, independent candidate is still
  locally accepted against a real throwaway Git repository while all
  of that is true.
* Scenario B (roadmap scenario 9: storage falls into RED): a
  hand-constructed, incorrectly-permissive artifact retention
  decision for a protected (non-Class-1) checkpoint is caught by the
  protected-work audit as CONFLICT, proving the safety net holds even
  against a hypothetical upstream regression; the real
  decide_artifact_retention output for the same artifact is
  independently confirmed SAFE.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from project_orchestrator.iron_god_acceptance import accept_candidate
from project_orchestrator.iron_god_artifact_retention import (
    CHECKPOINT_CLASS_ACCEPTED_MILESTONE,
    ArtifactEntry,
    ArtifactRetentionDecision,
    decide_artifact_retention,
)
from project_orchestrator.iron_god_capacity_model import (
    DEVELOPMENT,
    PUBLICATION,
    WorkerOccupancy,
    compute_available_capacity,
)
from project_orchestrator.iron_god_protected_work_audit import (
    CONFLICT,
    SAFE,
    audit_protected_work,
)
from project_orchestrator.iron_god_publication_job import PublicationJob
from project_orchestrator.iron_god_publication_lane import decide_active_publication
from project_orchestrator.iron_god_publication_state_machine import transition
from project_orchestrator.iron_god_transport_deferral import handle_transport_failure
from project_orchestrator.iron_god_verification_receipt import GateResult, build_receipt
from project_orchestrator.local_storage_pressure import BLOCKED


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "a.txt").write_text("1\n", encoding="utf-8")
    _git(path, "add", "a.txt")
    _git(path, "commit", "-q", "-m", "c1")
    return path


def _commit(path: Path, name: str) -> str:
    (path / name).write_text("x\n", encoding="utf-8")
    _git(path, "add", name)
    _git(path, "commit", "-q", "-m", name)
    return _git(path, "rev-parse", "HEAD")


def _green_receipt(head_sha: str, repository: str):
    gates = (GateResult("lint", 0, False, False), GateResult("tests", 0, False, False))
    return build_receipt(head_sha, repository, gates)


def _ready_job(publication_id: str, repository: str) -> PublicationJob:
    return PublicationJob(
        publication_id=publication_id,
        repository=repository,
        milestone_id="M-001",
        accepted_sha="a" * 40,
        parent_accepted_sha=None,
        github_base_sha="b" * 40,
        verification_receipt_ref="receipt-001",
        product_reality_receipt_ref=None,
        required_ci=("ci-main",),
        required_review_policy="one-approval",
        publication_branch="iron-god/pub-001",
        current_state="READY_TO_PUBLISH",
    )


def test_scenario_a_publication_stuck_development_continues(tmp_path):
    repository = "demo-repo"
    job = transition(_ready_job("pub-001", repository), "PUSHING")

    outcome = handle_transport_failure(job, prior_attempts=0)
    assert outcome.resulting_job.current_state == "PUBLICATION_DEFERRED"

    lane = decide_active_publication(repository, (outcome.resulting_job,))
    assert lane.active_publication_id is None
    assert lane.queued_publication_ids == ("pub-001",)

    occupancy = (
        WorkerOccupancy(worker_id="publisher-1", lane=PUBLICATION),
        WorkerOccupancy(worker_id="builder-1", lane=DEVELOPMENT),
    )
    capacity = compute_available_capacity(6, occupancy)
    assert capacity.occupied_development_slots == 1
    assert capacity.available_development_slots == 5
    assert capacity.excluded_publication_lane_workers == ("publisher-1",)

    repo = _init_repo(tmp_path / "repo")
    first_sha = _git(repo, "rev-parse", "HEAD")
    first_result = accept_candidate(
        repo,
        repository,
        _green_receipt(first_sha, repository),
        expected_parent_sha=None,
        milestone_id="M-first",
    )
    assert first_result.accepted_sha == first_sha

    second_sha = _commit(repo, "independent-slice.txt")
    second_result = accept_candidate(
        repo,
        repository,
        _green_receipt(second_sha, repository),
        expected_parent_sha=first_sha,
        milestone_id="M-second",
    )
    assert second_result.accepted_sha == second_sha
    assert second_result.previous_baseline_sha == first_sha


def test_scenario_b_storage_red_protected_work_never_silently_reclaimed():
    entry = ArtifactEntry(
        artifact_id="milestone-artifact-1",
        checkpoint_class=CHECKPOINT_CLASS_ACCEPTED_MILESTONE,
        in_active_use=False,
    )

    correct_decision = decide_artifact_retention(entry, pressure=BLOCKED)
    assert correct_decision.eligible_for_reclamation is False
    (correct_result,) = audit_protected_work(((entry, correct_decision),))
    assert correct_result.status == SAFE

    buggy_decision = ArtifactRetentionDecision(
        artifact_id="milestone-artifact-1",
        eligible_for_reclamation=True,
        reason="hypothetical upstream regression incorrectly claiming eligibility",
    )
    (buggy_result,) = audit_protected_work(((entry, buggy_decision),))
    assert buggy_result.status == CONFLICT
    assert buggy_result.safe_to_proceed is False
