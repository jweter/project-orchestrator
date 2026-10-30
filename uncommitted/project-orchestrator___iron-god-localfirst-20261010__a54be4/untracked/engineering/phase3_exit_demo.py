"""IG-036 Phase 3 exit demonstration in a disposable local clone.

The demo uses real Git commits, protected refs, isolated integration worktrees,
repository-native FULL preflight, SQLite acceptance prepare/finalize, and Git
accepted-ref CAS twice. It performs no GitHub operations and never advances the
accepted ref in the source repository.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from project_orchestrator.human_ownership import AUTHORIZED, owner_control_status
from project_orchestrator.local_acceptance import (
    commit_or_recover_prepared_acceptance,
    prepare_verified_acceptance,
)
from project_orchestrator.local_first_state import LocalFirstState
from project_orchestrator.local_git_refs import LocalGitRefs
from project_orchestrator.local_integration import LocalIntegrationController
from project_orchestrator.local_verification import (
    ORCHESTRATOR_NATIVE_CHECKS,
    VerificationPolicy,
)


def run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"GIT_FAILED:{args[0]}:{result.returncode}:{result.stderr.strip()[-500:]}"
        )
    return result.stdout.strip()


def require_owner(owner_state_root: Path) -> None:
    if owner_control_status(owner_state_root) != AUTHORIZED:
        raise RuntimeError("OWNER_CONTROL_VETO")


def native_preflight(worktree: Path, owner_state_root: Path) -> Path:
    require_owner(owner_state_root)
    result = subprocess.run(
        [sys.executable, "engineering/preflight.py"],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=False,
        timeout=900,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "NATIVE_PREFLIGHT_FAILED:"
            + (result.stdout + "\n" + result.stderr)[-4000:]
        )
    require_owner(owner_state_root)
    evidence = worktree / "preflight-evidence.json"
    if not evidence.is_file():
        raise RuntimeError("NATIVE_PREFLIGHT_RECEIPT_MISSING")
    return evidence


def create_candidate(
    *,
    clone: Path,
    root: Path,
    name: str,
    baseline: str,
    marker_text: str,
    refs: LocalGitRefs,
    owner_state_root: Path,
) -> str:
    require_owner(owner_state_root)
    worktree = root / f"candidate-{name}"
    run_git(clone, "worktree", "add", "--detach", "--", str(worktree), baseline)
    marker = worktree / "docs" / f"phase3-demo-{name}.txt"
    marker.write_text(marker_text + "\n", encoding="utf-8")
    run_git(worktree, "add", "--", str(marker.relative_to(worktree)))
    run_git(
        worktree,
        "-c",
        "user.name=Iron God Phase3 Demo",
        "-c",
        "user.email=iron-god-demo@local.invalid",
        "commit",
        "-m",
        f"Phase3 demo candidate {name}",
    )
    candidate = run_git(worktree, "rev-parse", "HEAD")
    refs.protect("candidate", f"phase3-{name}", candidate)
    require_owner(owner_state_root)
    return candidate


def accept_stage(
    *,
    stage,
    state: LocalFirstState,
    refs: LocalGitRefs,
    milestone_id: str,
    policy: VerificationPolicy,
    owner_state_root: Path,
) -> dict:
    evidence = native_preflight(stage.worktree, owner_state_root)
    prepared = prepare_verified_acceptance(
        repo=stage.worktree,
        state=state,
        refs=refs,
        milestone_id=milestone_id,
        accepted_base_sha=stage.accepted_base_sha,
        target_sha=stage.integrated_sha,
        evidence_path=evidence,
        policy=policy,
    )
    committed = commit_or_recover_prepared_acceptance(
        repo=stage.worktree,
        state=state,
        refs=refs,
        milestone_id=milestone_id,
        evidence_path=evidence,
        policy=policy,
    )
    if prepared.evidence_sha256 != committed.evidence_sha256:
        raise RuntimeError("PREFLIGHT_RECEIPT_CHANGED_DURING_ACCEPTANCE")
    return {
        "milestone_id": milestone_id,
        "accepted_sha": stage.integrated_sha,
        "evidence_sha256": committed.evidence_sha256,
        "result_count": committed.result_count,
        "product_reality": committed.product_reality,
    }


def run_demo(source_repo: Path, demo_root: Path, owner_state_root: Path) -> Path:
    source_repo = source_repo.resolve()
    demo_root = demo_root.absolute()
    owner_state_root = owner_state_root.resolve()
    require_owner(owner_state_root)
    if os.name == "nt":
        longest_known_suffix = (
            "/integrations/.preflight-tmp/phase3-two/"
            "test_dirty_disposable_worker_recovery_preserves_residue_then_cleans0/"
            "workspaces/.worker-worktrees/everward-worker-1/Project-Everward/tracked.txt"
        )
        if len(str(demo_root)) + len(longest_known_suffix) >= 240:
            raise RuntimeError("DEMO_ROOT_TOO_LONG_FOR_WINDOWS_GIT_TESTS")
    if demo_root.exists():
        raise RuntimeError("DEMO_ROOT_ALREADY_EXISTS")
    demo_root.mkdir(parents=True)

    clone = demo_root / "repo"
    clone_result = subprocess.run(
        ["git", "clone", "--no-local", "-q", str(source_repo), str(clone)],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    if clone_result.returncode != 0:
        raise RuntimeError("DEMO_CLONE_FAILED:" + clone_result.stderr.strip()[-1000:])

    baseline = run_git(clone, "rev-parse", "HEAD")
    source_head = run_git(source_repo, "rev-parse", "HEAD")
    if baseline != source_head:
        raise RuntimeError("DEMO_BASELINE_NOT_SOURCE_HEAD")

    refs = LocalGitRefs(clone, owner_state_root=owner_state_root)
    refs.bootstrap_accepted(baseline)
    state = LocalFirstState(demo_root / "phase3-state.sqlite3")
    policy = VerificationPolicy(
        repository="jweter/project-orchestrator",
        required_checks=ORCHESTRATOR_NATIVE_CHECKS,
        product_reality_required=False,
    )

    candidate_one = create_candidate(
        clone=clone,
        root=demo_root,
        name="one",
        baseline=baseline,
        marker_text="independent candidate one",
        refs=refs,
        owner_state_root=owner_state_root,
    )
    candidate_two = create_candidate(
        clone=clone,
        root=demo_root,
        name="two",
        baseline=baseline,
        marker_text="independent candidate two",
        refs=refs,
        owner_state_root=owner_state_root,
    )

    integration = LocalIntegrationController(
        clone,
        workspace_root=demo_root / "integrations",
        owner_state_root=owner_state_root,
    )
    stage_one = integration.stage(
        integration_id="phase3-one",
        candidate_id="phase3-one",
        candidate_base_sha=baseline,
        expected_accepted_sha=baseline,
    )
    milestone_one = accept_stage(
        stage=stage_one,
        state=state,
        refs=refs,
        milestone_id="phase3-demo-1",
        policy=policy,
        owner_state_root=owner_state_root,
    )
    if refs.accepted() != stage_one.integrated_sha:
        raise RuntimeError("FIRST_ACCEPTANCE_NOT_CURRENT")

    stage_two = integration.stage(
        integration_id="phase3-two",
        candidate_id="phase3-two",
        candidate_base_sha=baseline,
        expected_accepted_sha=stage_one.integrated_sha,
    )
    milestone_two = accept_stage(
        stage=stage_two,
        state=state,
        refs=refs,
        milestone_id="phase3-demo-2",
        policy=policy,
        owner_state_root=owner_state_root,
    )
    if refs.accepted() != stage_two.integrated_sha:
        raise RuntimeError("SECOND_ACCEPTANCE_NOT_CURRENT")
    if state.pending_acceptances():
        raise RuntimeError("PENDING_ACCEPTANCE_AFTER_DEMO")

    receipt = demo_root / "phase3-exit-demo-receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "PASS",
                "scope": "DISPOSABLE_LOCAL_CLONE",
                "github_used": False,
                "generated_at": datetime.now(UTC).isoformat(),
                "source_head": source_head,
                "baseline": baseline,
                "candidate_one": candidate_one,
                "candidate_two": candidate_two,
                "milestone_one": milestone_one,
                "milestone_two": milestone_two,
                "final_accepted_sha": refs.accepted(),
                "pending_acceptances": 0,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--demo-root", type=Path, required=True)
    parser.add_argument("--owner-state-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        receipt = run_demo(args.source_repo, args.demo_root, args.owner_state_root)
    except Exception as exc:
        print(f"PHASE3 LOCAL DEMO: FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"PHASE3 LOCAL DEMO: PASS receipt={receipt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
