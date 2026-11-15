"""Real disposable-Git + SQLite three-microedit restart canary (#710).

Uses the existing exact-head local acceptance and protected-ref controller,
not a new execution path. All source edits are deterministic test fixtures,
NOT Ollama output; this verifies durable plumbing only, not cloud-free live
model capability or native product acceptance for production repositories.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from project_orchestrator.local_acceptance import (
    commit_or_recover_prepared_acceptance,
    prepare_verified_acceptance,
)
from project_orchestrator.local_acceptance_refill import select_next_after_acceptance
from project_orchestrator.local_first_state import LocalFirstState
from project_orchestrator.local_git_refs import LocalGitRefs
from project_orchestrator.local_verification import VerificationPolicy
from tests.owner_authority import grant_owner_authority, revoke_owner_authority

REPOSITORY = "jweter/Test"
MILESTONES = ("micro-alpha", "micro-beta", "micro-gamma")
SLICES = ("IG-alpha", "IG-beta", "IG-gamma")
VALUES = (2, 5, 9)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=25,
    ).stdout.strip()


def native_test(repo: Path, keys: tuple[str, ...], *, must_pass: bool) -> str:
    expression = " or ".join(keys)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/test_micro.py", "-k", expression],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=45,
    )
    output = result.stdout + "\n" + result.stderr
    if must_pass:
        assert result.returncode == 0, output[-2000:]
    else:
        assert result.returncode != 0, "Expected a real native red test"
        assert "FAILED" in output and "AssertionError" in output, output[-2000:]
    return output[-1800:]


def evidence(repo: Path, target: str, keys: tuple[str, ...], folder: Path) -> Path:
    out = native_test(repo, keys, must_pass=True)
    path = folder / f"{target}.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "repository": REPOSITORY,
                "head_sha": target,
                "mode": "FULL",
                "status": "GREEN",
                "active_check": None,
                "results": [
                    {
                        "name": "tests",
                        "returncode": 0,
                        "timed_out": False,
                        "interrupted": False,
                        "output_tail": out,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def accept(
    repo: Path,
    state: LocalFirstState,
    refs: LocalGitRefs,
    policy: VerificationPolicy,
    folder: Path,
    *,
    index: int,
    base: str,
) -> str:
    key, expected = MILESTONES[index], VALUES[index]
    # Fail the actual targeted test before editing, not just a mocked oracle.
    native_test(repo, (key.removeprefix("micro-"),), must_pass=False)
    with (repo / "helpers.py").open("a", encoding="utf-8") as stream:
        stream.write(
            f"\ndef {key.removeprefix('micro-')}() -> int:\n    return {expected}\n"
        )
    git(repo, "add", "helpers.py")
    git(repo, "commit", "-qm", key)
    sha = git(repo, "rev-parse", "HEAD")
    test_keys = tuple(s.removeprefix("micro-") for s in MILESTONES[: index + 1])
    receipt = evidence(repo, sha, test_keys, folder)
    prepare_verified_acceptance(
        repo=repo,
        state=state,
        refs=refs,
        milestone_id=key,
        accepted_base_sha=base,
        target_sha=sha,
        evidence_path=receipt,
        policy=policy,
    )
    result = commit_or_recover_prepared_acceptance(
        repo=repo,
        state=state,
        refs=refs,
        milestone_id=key,
        evidence_path=receipt,
        policy=policy,
    )
    assert result.head_sha == sha
    assert refs.accepted() == sha
    return sha


def next_ready(
    state: LocalFirstState,
    refs: LocalGitRefs,
    owner: Path,
    *,
    prior_index: int,
    sha: str,
):
    next_id = SLICES[prior_index + 1]
    packet = {
        "slice_id": next_id,
        "state": "ELIGIBLE",
        "repository": REPOSITORY,
        "exact_base_sha": sha,
        "requires_milestones": [MILESTONES[prior_index]],
        "worktree_name": f"micro-worktree-{prior_index + 1}",
        "branch": f"iron-god/canary-{prior_index + 1}",
    }
    return select_next_after_acceptance(
        state=state,
        refs=refs,
        owner_state_root=owner,
        milestone_id=MILESTONES[prior_index],
        controller={
            "mode": "candidate_mode",
            "candidate_execution_authorized": True,
            "slices": [{"id": next_id, "state": "ELIGIBLE"}],
        },
        handoffs={next_id: packet},
    )


def test_three_actual_commits_and_native_tests_survive_restart_after_second(
    tmp_path: Path,
) -> None:
    repo, owner = tmp_path / "repo", tmp_path / "owner"
    repo.mkdir()
    owner.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Iron God disposable canary")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    (repo / "helpers.py").write_text(
        '"""Incremental microedit canary."""\n', encoding="utf-8"
    )
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_micro.py").write_text(
        "import helpers\n"
        + "\n".join(
            f"def test_{key}():\n"
            f"    assert getattr(helpers, '{key}', lambda: None)() == {expected}\n"
            for key, expected in zip(("alpha", "beta", "gamma"), VALUES)
        ),
        encoding="utf-8",
    )
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "original red acceptance tests")
    first_base = git(repo, "rev-parse", "HEAD")

    grant_owner_authority(owner)
    refs = LocalGitRefs(repo, owner_state_root=owner)
    refs.bootstrap_accepted(first_base)
    db_path = tmp_path / "canary-state.sqlite3"
    state = LocalFirstState(db_path)
    policy = VerificationPolicy(
        repository=REPOSITORY,
        required_checks=("tests",),
        product_reality_required=False,  # Disposable fixture only, not a product release.
    )
    sha_a = accept(repo, state, refs, policy, tmp_path, index=0, base=first_base)
    next_b = next_ready(state, refs, owner, prior_index=0, sha=sha_a)
    assert next_b.status == "READY_FOR_LEASED_DISPATCH"
    assert next_b.next_slice_id == "IG-beta"
    sha_b = accept(repo, state, refs, policy, tmp_path, index=1, base=sha_a)
    next_c = next_ready(state, refs, owner, prior_index=1, sha=sha_b)
    assert next_c.status == "READY_FOR_LEASED_DISPATCH"
    assert next_c.next_slice_id == "IG-gamma"

    # Pause AFTER the second meaningful, tested, committed source edit.
    revoke_owner_authority(owner)
    assert (
        next_ready(state, refs, owner, prior_index=1, sha=sha_b).reason
        == "OWNER_CONTROL_VETO"
    )

    # Simulate supervisor process restart: reload both independent durable
    # state stores. No source edit, new attempt, or new milestone is replayed.
    del refs, state
    refs = LocalGitRefs(repo, owner_state_root=owner)
    state = LocalFirstState(db_path)
    assert refs.accepted() == sha_b
    assert len(state.snapshot()["milestones"]) == 2
    grant_owner_authority(owner)
    resumed = next_ready(state, refs, owner, prior_index=1, sha=sha_b)
    assert resumed == next_c
    assert resumed.accepted_sha == sha_b

    sha_c = accept(repo, state, refs, policy, tmp_path, index=2, base=sha_b)
    assert refs.accepted() == sha_c
    assert int(git(repo, "rev-list", "--count", f"{first_base}..{sha_c}")) == 3
    assert len({sha_a, sha_b, sha_c}) == 3
    for sha in (sha_a, sha_b, sha_c):
        assert (
            git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", sha)
            == "helpers.py"
        )
    assert len(state.snapshot()["milestones"]) == 3

    # A second state-store reopen never creates another milestone/receipt.
    del refs, state
    refs = LocalGitRefs(repo, owner_state_root=owner)
    state = LocalFirstState(db_path)
    accepted_events = [
        e
        for e in state.snapshot()["journal"]
        if e["event_type"] == "MILESTONE_ACCEPTED"
    ]
    assert len(accepted_events) == 3
    assert len({e["milestone_id"] for e in accepted_events}) == 3
    assert refs.accepted() == sha_c
    native_test(repo, ("alpha", "beta", "gamma"), must_pass=True)
