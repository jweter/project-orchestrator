"""Real subprocess termination after edit two, then exact-head durable resume (#710).

The parent kills a separately running Python worker through Popen.kill().
No installed supervisor, real repository, engine grant, or Ollama process is used.
The test exercises the EXISTING local_acceptance SQLite/protected Git refs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from project_orchestrator.local_first_state import LocalFirstState
from project_orchestrator.local_git_refs import LocalGitRefs
from project_orchestrator.local_verification import VerificationPolicy
from tests.owner_authority import grant_owner_authority, revoke_owner_authority
from tests.test_iron_god_three_microedit_resume_canary import (
    MILESTONES,
    REPOSITORY,
    VALUES,
    accept,
    git,
    native_test,
    next_ready,
)

ROOT = Path(__file__).resolve().parents[1]


def prepare(folder: Path) -> str:
    repo, owner = folder / "repo", folder / "owner"
    repo.mkdir()
    owner.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Disposable Iron God crash test")
    git(repo, "config", "user.email", "crash-test@example.invalid")
    (repo / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    (repo / "helpers.py").write_text(
        '"""Temporary source for real crash-recovery test."""\n', encoding="utf-8"
    )
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_micro.py").write_text(
        "import helpers\n"
        + "\n".join(
            f"def test_{name}():\n"
            f"    assert getattr(helpers, '{name}', lambda: None)() == {value}\n"
            for name, value in zip(("alpha", "beta", "gamma"), VALUES, strict=True)
        ),
        encoding="utf-8",
    )
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "initial 3 native failing tests")
    base = git(repo, "rev-parse", "HEAD")
    grant_owner_authority(owner)
    LocalGitRefs(repo, owner_state_root=owner).bootstrap_accepted(base)
    return base


def worker(folder: Path, phase: str) -> int:
    repo, owner = folder / "repo", folder / "owner"
    state = LocalFirstState(folder / "acceptance.sqlite3")
    refs = LocalGitRefs(repo, owner_state_root=owner)
    policy = VerificationPolicy(
        repository=REPOSITORY,
        required_checks=("tests",),
        product_reality_required=False,  # disposable regression only
    )
    if phase == "first-two":
        if state.snapshot()["milestones"]:
            return 42
        base = refs.accepted()
        first = accept(repo, state, refs, policy, folder, index=0, base=base)
        ready_b = next_ready(state, refs, owner, prior_index=0, sha=first)
        if ready_b.status != "READY_FOR_LEASED_DISPATCH":
            return 43
        second = accept(repo, state, refs, policy, folder, index=1, base=first)
        ready_c = next_ready(state, refs, owner, prior_index=1, sha=second)
        if ready_c.status != "READY_FOR_LEASED_DISPATCH":
            return 44
        # The parent observes this marker only AFTER both durable commits,
        # receipts and acceptance journal entries exist.
        (folder / "second-accepted.json").write_text(
            json.dumps({"accepted_head": second, "milestones": 2}),
            encoding="utf-8",
        )
        # The parent terminates the process without cleanup via Popen.kill().
        while True:
            time.sleep(0.25)

    elif phase == "third":
        seen = state.snapshot()["milestones"]
        if len(seen) == 3:
            print(
                json.dumps({"status": "ALREADY_COMPLETE", "sha": refs.accepted()}),
                flush=True,
            )
            return 0
        if len(seen) != 2:
            return 45
        base = refs.accepted()
        ready_c = next_ready(state, refs, owner, prior_index=1, sha=base)
        if ready_c.status != "READY_FOR_LEASED_DISPATCH":
            return 46
        final = accept(repo, state, refs, policy, folder, index=2, base=base)
        print(json.dumps({"status": "THIRD_COMMITTED", "sha": final}), flush=True)
        return 0
    return 47


def child_command(folder: Path, phase: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "tests.test_iron_god_force_kill_recovery",
        phase,
        str(folder),
    ]


def child_env() -> dict[str, str]:
    # pytest's local package-path insertion does not survive a process
    # boundary. Preserve the source checkout without installing a release.
    return {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT)]),
    }


def test_forced_worker_process_kill_resumes_only_third_verified_change(
    tmp_path: Path,
) -> None:
    base = prepare(tmp_path)
    repo, owner = tmp_path / "repo", tmp_path / "owner"
    marker = tmp_path / "second-accepted.json"
    proc = subprocess.Popen(
        child_command(tmp_path, "first-two"),
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=child_env(),
    )
    try:
        deadline = time.monotonic() + 75
        while not marker.exists() and time.monotonic() < deadline:
            if proc.poll() is not None:
                raise AssertionError(
                    f"child died before two commits: {proc.returncode}"
                )
            time.sleep(0.2)
        assert marker.exists(), "worker did not durably accept second edit"
        announced = json.loads(marker.read_text(encoding="utf-8"))
        assert announced["milestones"] == 2
        assert proc.poll() is None
    finally:
        if proc.poll() is None:
            proc.kill()  # real OS process termination, no graceful Python cleanup
        proc.communicate(timeout=10)

    assert proc.returncode != 0
    state = LocalFirstState(tmp_path / "acceptance.sqlite3")
    refs = LocalGitRefs(repo, owner_state_root=owner)
    assert len(state.snapshot()["milestones"]) == 2
    second = refs.accepted()
    assert second == announced["accepted_head"]
    assert int(git(repo, "rev-list", "--count", f"{base}..{second}")) == 2
    assert (
        next_ready(state, refs, owner, prior_index=1, sha=second).status
        == "READY_FOR_LEASED_DISPATCH"
    )

    # Even a correct durable receipt is not permission to resume under Give
    # Me Control. The new process must observe a NEW owner control grant.
    revoke_owner_authority(owner)
    assert (
        next_ready(state, refs, owner, prior_index=1, sha=second).reason
        == "OWNER_CONTROL_VETO"
    )
    denied = subprocess.run(
        child_command(tmp_path, "third"),
        cwd=ROOT,
        env=child_env(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert denied.returncode == 46
    assert refs.accepted() == second
    assert len(state.snapshot()["milestones"]) == 2

    grant_owner_authority(owner)
    resumed = subprocess.run(
        child_command(tmp_path, "third"),
        cwd=ROOT,
        env=child_env(),
        capture_output=True,
        text=True,
        timeout=75,
    )
    assert resumed.returncode == 0, resumed.stderr[-1800:]
    assert json.loads(resumed.stdout)["status"] == "THIRD_COMMITTED"
    fresh_refs = LocalGitRefs(repo, owner_state_root=owner)
    fresh_state = LocalFirstState(tmp_path / "acceptance.sqlite3")
    final = fresh_refs.accepted()
    assert final != second
    assert int(git(repo, "rev-list", "--count", f"{base}..{final}")) == 3
    assert len(fresh_state.snapshot()["milestones"]) == 3
    accepted = [
        e
        for e in fresh_state.snapshot()["journal"]
        if e["event_type"] == "MILESTONE_ACCEPTED"
    ]
    assert len(accepted) == 3
    assert {e["milestone_id"] for e in accepted} == set(MILESTONES)
    native_test(repo, ("alpha", "beta", "gamma"), must_pass=True)

    # Third process invocation is idempotent: no fourth commit or milestone.
    repeated = subprocess.run(
        child_command(tmp_path, "third"),
        cwd=ROOT,
        env=child_env(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert repeated.returncode == 0
    assert json.loads(repeated.stdout)["status"] == "ALREADY_COMPLETE"
    assert LocalGitRefs(repo, owner_state_root=owner).accepted() == final
    assert (
        len(LocalFirstState(tmp_path / "acceptance.sqlite3").snapshot()["milestones"])
        == 3
    )


if __name__ == "__main__":
    raise SystemExit(worker(Path(sys.argv[2]), sys.argv[1]))
