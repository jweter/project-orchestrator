"""Github branch is data, not owner authorization or a worker lease."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from project_orchestrator import iron_god_offload as offload
from project_orchestrator import iron_god_offload_git_intake as intake


def git(repo: Path, *args: str) -> str:
    run = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=False,
    )
    assert run.returncode == 0, run.stderr
    return run.stdout.strip()


def world(tmp_path: Path, *, claim: bool = True, unexpected_file: bool = False):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Offload Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "src").mkdir()
    (repo / "src" / "unit.py").write_text(
        "def answer():\n    return 1\n", encoding="utf-8",
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    db = tmp_path / "private" / "coordination.sqlite"
    db.parent.mkdir()
    offer = offload.offer_slice(
        db, repository="jweter/project-orchestrator",
        slice_id="ORCH-GITHUB-TRANSPORT-1", base_sha=base,
        allowed_paths=["src/unit.py", "tests/test_unit.py"],
        authority_ref="issue:747", failure_ref="packet:verified-failure-1",
    )
    if claim:
        offload.claim_slice(db, offer["offer_id"])
    (repo / "src" / "unit.py").write_text(
        "def answer():\n    return 2\n", encoding="utf-8",
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_unit.py").write_text(
        "from src.unit import answer\n\ndef test_answer():\n    assert answer() == 2\n",
        encoding="utf-8",
    )
    if unexpected_file:
        (repo / "src" / "injected.py").write_text("unapproved", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "implement difficult unit")
    candidate = git(repo, "rev-parse", "HEAD")
    ref = f"refs/remotes/origin/offload/chatgpt/{offer['offer_id']}"
    git(repo, "update-ref", ref, candidate)
    return repo, db, offer, base, candidate, ref


def stage(tmp_path, world_data, **changes):
    repo, db, offer, base, candidate, _ = world_data
    args = dict(
        db_path=db, stage_root=tmp_path / "stage",
        repository_checkout=repo, offer_id=offer["offer_id"],
        candidate_head=candidate, observed_base_head=base,
    )
    args.update(changes)
    return intake.stage_fetched_github_candidate(**args)


def test_fetched_github_commit_to_immutable_sqlite_and_isolated_import(tmp_path):
    data = world(tmp_path)
    result = stage(tmp_path, data)
    assert result["status"] == "STAGED_UNVERIFIED"
    assert result["execution_authorized"] is False
    assert result["worker_lease_granted"] is False
    assert result["merge_authorized"] is False
    assert result["changed_paths"] == ["src/unit.py", "tests/test_unit.py"]
    assert stage(tmp_path, data) == result  # replay never overwrites bytes
    repo, db, offer, base, candidate, _ = data
    verified = offload.read_offer(db, offer["offer_id"])
    assert verified["base_sha"] == base
    receipt = {
        "offer_id": result["offer_id"],
        "patch_sha256": result["patch_sha256"],
        "manifest_sha256": result["manifest_sha256"],
        "manifest_path": result["manifest_path"],
    }
    patch = offload.verify_candidate(verified, receipt)
    assert b"def answer():" in patch
    assert offload.list_offload_proposals(db)["items"][0]["status"] == "STAGED_UNVERIFIED"

    # This intentionally uses explicitly scoped TEST callbacks. No real owner
    # control, worker claim, PR publication or laptop install is made.
    imported = offload.prepare_isolated_import(
        verified, receipt, db_path=db, stage_root=tmp_path / "stage",
        clone=repo, destination=tmp_path / "isolated-worktree",
        authority_check=lambda: True, ownership_check=lambda: True,
    )
    assert imported["status"] == "PREFLIGHT_UNVERIFIED"
    assert imported["merge_authorized"] is False
    dest = Path(imported["worktree"])
    assert git(dest, "rev-parse", "HEAD") == base
    assert (dest / "src" / "unit.py").read_text(encoding="utf-8").splitlines()[-1] == (
        "    return 2"
    )
    assert git(repo, "rev-parse", "HEAD") == candidate


def test_missing_chatgpt_claim_cannot_stage_artifact(tmp_path):
    data = world(tmp_path, claim=False)
    with pytest.raises(offload.OffloadRejected, match="CHATGPT_ADVISORY_RESERVATION_REQUIRED"):
        stage(tmp_path, data)
    assert not (tmp_path / "stage").exists()


def test_extra_protected_file_in_github_commit_is_rejected(tmp_path):
    data = world(tmp_path, unexpected_file=True)
    with pytest.raises(offload.OffloadRejected, match="GITHUB_BRANCH_PATH_FENCE_VIOLATION"):
        stage(tmp_path, data)
    assert offload.list_offload_proposals(data[1])["items"][0]["status"] == "RESERVED"


def test_stale_or_changed_remote_head_fails_closed(tmp_path):
    data = world(tmp_path)
    with pytest.raises(offload.OffloadRejected, match="STALE_GITHUB_BASE_HEAD"):
        stage(tmp_path, data, observed_base_head="f" * 40)
    with pytest.raises(offload.OffloadRejected, match="FETCHED_BRANCH_HEAD_CHANGED"):
        stage(tmp_path, data, candidate_head="f" * 40)


def test_multiple_unreviewed_commits_cannot_inherit_exact_base_claim(tmp_path):
    data = world(tmp_path)
    repo, _, _, _, _, ref = data
    (repo / "src" / "unit.py").write_text(
        "def answer():\n    return 3\n", encoding="utf-8",
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "unreviewed second commit")
    second = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", ref, second)
    with pytest.raises(offload.OffloadRejected, match="SINGLE_EXACT_BASE_PARENT_REQUIRED"):
        stage(tmp_path, data, candidate_head=second)


def test_unfetched_remote_ref_has_no_authority(tmp_path):
    data = world(tmp_path)
    repo, _, _, _, _, ref = data
    git(repo, "update-ref", "-d", ref)
    with pytest.raises(offload.OffloadRejected, match="GIT_EVIDENCE_UNAVAILABLE"):
        stage(tmp_path, data)
