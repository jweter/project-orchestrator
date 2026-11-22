"""Guarded ChatGPT offload pilot: SQLite replay, artifact integrity, safe import.

These tests simulate the handoff without enabling or modifying a resident worker.
Successful import is still PREFLIGHT_UNVERIFIED and grants no merge or release.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from project_orchestrator import iron_god_offload as offload


REPO = "jweter/VoiceStudio"
SLICE = "VS-OFFLOAD-CANARY-1"


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def case(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "src").mkdir()
    (repo / "src" / "feature.py").write_text("def score():\n    return 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=Canary", "-c", "user.email=canary@localhost",
        "commit", "-q", "-m", "baseline")
    base = git(repo, "rev-parse", "HEAD").stdout.decode().strip()
    db = tmp_path / "sqlite" / "coordination.sqlite"
    db.parent.mkdir()
    offer = offload.offer_slice(
        db, repository=REPO, slice_id=SLICE, base_sha=base,
        allowed_paths=["src/feature.py"], authority_ref="issue:747",
        failure_ref="evidence:canary-1",
    )
    return tmp_path, db, repo, base, offer


def patch_for(repo: Path) -> bytes:
    source = repo / "src" / "feature.py"
    source.write_text("def score():\n    return 2\n", encoding="utf-8")
    patch = git(repo, "diff", "--binary", "HEAD").stdout
    git(repo, "restore", "--", "src/feature.py")
    return patch


def test_uninitialized_read_does_not_create_sqlite(tmp_path):
    db = tmp_path / "coordination.sqlite"
    with pytest.raises(offload.OffloadRejected, match="OFFER_DATABASE_UNAVAILABLE"):
        offload.read_offer(db, "1" * 64)
    assert not db.exists()


def test_durable_one_slice_claim_and_conflicting_replay(case):
    _, db, repo, base, offer = case
    again = offload.offer_slice(
        db, repository=REPO, slice_id=SLICE, base_sha=base,
        allowed_paths=["src/feature.py"], authority_ref="issue:747",
        failure_ref="evidence:canary-1",
    )
    assert again == offer
    with pytest.raises(offload.OffloadRejected, match="CONFLICTING_OFFER_REPLAY"):
        offload.offer_slice(
            db, repository=REPO, slice_id=SLICE, base_sha=base,
            allowed_paths=["src/other.py"], authority_ref="issue:747",
            failure_ref="evidence:canary-1",
        )
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: offload.claim_slice(db, offer["offer_id"]), range(4)))
    assert sum(not result["replayed"] for result in results) == 1
    assert all(result["worker_lease_granted"] is False for result in results)
    assert offload.read_offer(db, offer["offer_id"]) == offer
    with pytest.raises(offload.OffloadRejected, match="ONLY_CHATGPT"):
        offload.claim_slice(db, offer["offer_id"], claimer="claude-code")


def test_immutable_candidate_replay_and_corruption(case):
    tmp, db, repo, _, offer = case
    offload.claim_slice(db, offer["offer_id"])
    patch = patch_for(repo)
    candidate = offload.seal_candidate(offer, patch, tmp / "staging")
    assert offload.verify_candidate(offer, candidate) == patch
    assert offload.register_candidate(db, candidate)["status"] == "STAGED_UNVERIFIED"
    assert offload.register_candidate(db, candidate)["status"] == "STAGED_UNVERIFIED"
    assert offload.seal_candidate(offer, patch, tmp / "staging") == candidate
    Path(candidate["manifest_path"]).write_text('{"tampered":true}', encoding="utf-8")
    with pytest.raises(offload.OffloadRejected, match="MANIFEST_HASH_MISMATCH"):
        offload.verify_candidate(offer, candidate)
    with pytest.raises(offload.OffloadRejected, match="IMMUTABLE_ARTIFACT_COLLISION"):
        offload.seal_candidate(offer, patch, tmp / "staging")


def test_registration_requires_prior_reservation(case):
    tmp, db, repo, _, offer = case
    candidate = offload.seal_candidate(offer, patch_for(repo), tmp / "staging")
    with pytest.raises(offload.OffloadRejected, match="CHATGPT_RESERVATION_REQUIRED"):
        offload.register_candidate(db, candidate)


def test_path_fence_protected_paths_binary_and_rename_are_rejected(case):
    tmp, db, repo, _, offer = case
    patch = patch_for(repo)
    assert offload.changed_paths(patch) == ["src/feature.py"]
    with pytest.raises(offload.OffloadRejected, match="PATCH_EXCEEDS_APPROVED"):
        offload.seal_candidate(offer, patch.replace(b"src/feature.py", b"tests/otherfile.py"),
                               tmp / "staging")
    with pytest.raises(offload.OffloadRejected, match="PROTECTED_CHANGED_PATH"):
        offload.offer_slice(
            db, repository=REPO, slice_id="BAD", base_sha=offer["base_sha"],
            allowed_paths=[".github/workflows/ci.yml"], authority_ref="issue:747",
            failure_ref="evidence:canary-1",
        )
    with pytest.raises(offload.OffloadRejected, match="UNSUPPORTED_DESTRUCTIVE"):
        offload.changed_paths(patch + b"\nGIT binary patch\n")
    with pytest.raises(offload.OffloadRejected, match="UNSAFE_PATCH_HEADER"):
        offload.changed_paths(patch.replace(b"b/src/feature.py", b"b/src/other.py", 1))


def test_authority_and_owner_claim_required_before_git_mutation(case):
    tmp, db, repo, _, offer = case
    offload.claim_slice(db, offer["offer_id"])
    candidate = offload.seal_candidate(offer, patch_for(repo), tmp / "staging")
    offload.register_candidate(db, candidate)
    destination = tmp / "isolated"
    with pytest.raises(offload.OffloadRejected, match="INDEPENDENT_OWNER_AND_LEASE"):
        offload.prepare_isolated_import(
            offer, candidate, db_path=db, stage_root=tmp / "staging",
            clone=repo, destination=destination,
        )
    assert not destination.exists()
    with pytest.raises(offload.OffloadRejected, match="OWNER_OR_WORKER"):
        offload.prepare_isolated_import(
            offer, candidate, db_path=db, stage_root=tmp / "staging", clone=repo, destination=destination,
            authority_check=lambda: False, ownership_check=lambda: True,
        )
    assert not destination.exists()


def test_real_git_canary_import_then_local_targeted_verification(case):
    tmp, db, repo, base, offer = case
    offload.claim_slice(db, offer["offer_id"])
    candidate = offload.seal_candidate(offer, patch_for(repo), tmp / "staging")
    offload.register_candidate(db, candidate)
    gate_reads = {"owner": 0, "ownership": 0}

    def owner():
        gate_reads["owner"] += 1
        return True

    def ownership():
        gate_reads["ownership"] += 1
        return True

    result = offload.prepare_isolated_import(
        offer, candidate, db_path=db, stage_root=tmp / "staging", clone=repo, destination=tmp / "isolated",
        authority_check=owner, ownership_check=ownership,
    )
    assert result["status"] == "PREFLIGHT_UNVERIFIED"
    assert result["merge_authorized"] is False
    assert result["product_reality_verified"] is False
    assert result["base_sha"] == base
    assert gate_reads["owner"] >= 3 and gate_reads["ownership"] >= 3
    assert (tmp / "isolated" / "src" / "feature.py").read_text() == (
        "def score():\n    return 2\n"
    )
    assert (repo / "src" / "feature.py").read_text() == "def score():\n    return 1\n"
    verified = subprocess.run(
        [sys.executable, "-m", "py_compile", str(tmp / "isolated" / "src" / "feature.py")],
        capture_output=True, check=False,
    )
    assert verified.returncode == 0
    assert result["next_gate"] == "REPOSITORY_NATIVE_PREFLIGHT_THEN_EXISTING_PUBLICATION_LANE"
    assert hashlib.sha256(offload.verify_candidate(offer, candidate)).hexdigest() == candidate["patch_sha256"]


def test_mismatched_base_and_modified_patch_are_refused(case):
    tmp, db, repo, _, offer = case
    offload.claim_slice(db, offer["offer_id"])
    patch = patch_for(repo)
    candidate = offload.seal_candidate(offer, patch, tmp / "staging")
    offload.register_candidate(db, candidate)
    path = tmp / "staging" / (offer["offer_id"] + "-" + candidate["patch_sha256"] + ".patch")
    path.write_bytes(patch + b"\n# modified")
    with pytest.raises(offload.OffloadRejected, match="PATCH_HASH_MISMATCH"):
        offload.prepare_isolated_import(
            offer, candidate, db_path=db, stage_root=tmp / "staging", clone=repo, destination=tmp / "isolated",
            authority_check=lambda: True, ownership_check=lambda: True,
        )
    assert not (tmp / "isolated").exists()


def test_offer_malformed_identity_fails_before_database_creation(tmp_path):
    db = tmp_path / "mail.sqlite"
    for paths in (["../token"], ["docs/README.md"], ["src/../unsafe.py"],
                  ["src/file.py", "src/file.py"]):
        with pytest.raises(offload.OffloadRejected):
            offload.offer_slice(
                db, repository=REPO, slice_id="VALID", base_sha="f" * 40,
                allowed_paths=paths, authority_ref="issue:747",
                failure_ref="evidence:canary-1",
            )
    assert not db.exists()


def test_unregistered_artifact_and_untrusted_stage_are_refused(case):
    tmp, db, repo, base, offer = case
    candidate = offload.seal_candidate(offer, patch_for(repo), tmp / "staging")
    with pytest.raises(offload.OffloadRejected, match="REGISTERED_CHATGPT_ARTIFACT_REQUIRED"):
        offload.prepare_isolated_import(
            offer, candidate, db_path=db, stage_root=tmp / "staging",
            clone=repo, destination=tmp / "isolated",
            authority_check=lambda: True, ownership_check=lambda: True,
        )
    assert not (tmp / "isolated").exists()

    offload.claim_slice(db, offer["offer_id"])
    offload.register_candidate(db, candidate)
    with pytest.raises(offload.OffloadRejected, match="STAGING_ROOT_MISMATCH"):
        offload.prepare_isolated_import(
            offer, candidate, db_path=db, stage_root=tmp / "elsewhere",
            clone=repo, destination=tmp / "isolated",
            authority_check=lambda: True, ownership_check=lambda: True,
        )
    assert not (tmp / "isolated").exists()


def test_mcp_offload_proposal_view_exposes_no_authority_or_code(case):
    _, db, _, _, offer = case
    first = offload.list_offload_proposals(db, limit=1)
    assert first["execution_authorized"] is False
    assert first["items"][0]["status"] == "PROPOSED"
    offload.claim_slice(db, offer["offer_id"])
    second = offload.list_offload_proposals(db)
    assert second["items"][0]["status"] == "RESERVED"
    assert "allowed_paths" not in second["items"][0]
    with pytest.raises(offload.OffloadRejected, match="INVALID_OFFLOAD_PAGE_SIZE"):
        offload.list_offload_proposals(db, limit=500)


def test_owner_revocation_between_check_and_apply_keeps_tree_unmodified(case):
    tmp, db, repo, _, offer = case
    offload.claim_slice(db, offer["offer_id"])
    candidate = offload.seal_candidate(offer, patch_for(repo), tmp / "staging")
    offload.register_candidate(db, candidate)
    calls = {"count": 0}

    def owner():
        calls["count"] += 1
        return calls["count"] < 4

    destination = tmp / "isolated"
    with pytest.raises(offload.OffloadRejected, match="OWNER_OR_WORKER"):
        offload.prepare_isolated_import(
            offer, candidate, db_path=db, stage_root=tmp / "staging",
            clone=repo, destination=destination,
            authority_check=owner, ownership_check=lambda: True,
        )
    assert calls["count"] == 4
    assert (destination / "src" / "feature.py").read_text() == "def score():\n    return 1\n"
    assert git(destination, "status", "--porcelain").stdout.strip() == b""


def test_candidate_refuses_executable_mode_and_oversized_patch(case):
    _, _, repo, _, _ = case
    patch = patch_for(repo)
    with pytest.raises(offload.OffloadRejected, match="UNSUPPORTED_DESTRUCTIVE"):
        offload.changed_paths(patch.replace(
            b"diff --git a/src/feature.py b/src/feature.py",
            b"diff --git a/src/feature.py b/src/feature.py\nnew file mode 100755",
            1,
        ))
    with pytest.raises(offload.OffloadRejected, match="PATCH_SIZE"):
        offload.changed_paths(patch + b"\n" * offload.MAX_PATCH_BYTES)


def test_codex_review_malformed_deletion_patch_is_rejected_before_apply(case):
    """A producer cannot omit 'deleted file mode' and smuggle +++ /dev/null."""
    _, _, repo, _, _ = case
    patch = patch_for(repo)
    assert b"+++ b/src/feature.py" in patch
    malicious = patch.replace(b"+++ b/src/feature.py", b"+++ /dev/null")
    with pytest.raises(offload.OffloadRejected, match="DELETION_PATCH_FORBIDDEN"):
        offload.changed_paths(malicious)


def test_codex_review_symlink_index_mode_is_rejected(case):
    """Symlink target modifications encode mode 120000 on the index line."""
    _, _, repo, _, _ = case
    patch = patch_for(repo)
    assert b" 100644" in patch
    malicious = patch.replace(b" 100644", b" 120000", 1)
    with pytest.raises(offload.OffloadRejected, match="UNSAFE_INDEX_OBJECT_MODE"):
        offload.changed_paths(malicious)


def test_real_git_base_tree_symlink_is_rejected_without_worktree_mutation(tmp_path):
    """Check the actual Git tree mode even if untrusted patch text lies about it."""
    repo = tmp_path / "symlink-base"
    repo.mkdir()
    git(repo, "init", "-q")
    sha = subprocess.run(
        ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
        input=b"../../outside", capture_output=True, check=True,
    ).stdout.decode().strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"120000,{sha},src/link")
    git(repo, "-c", "user.name=Canary", "-c", "user.email=canary@localhost",
        "commit", "-q", "-m", "symlink base")
    base = git(repo, "rev-parse", "HEAD").stdout.decode().strip()
    assert b"120000" in git(repo, "ls-tree", base, "--", "src/link").stdout
    with pytest.raises(offload.OffloadRejected, match="BASE_TREE_SYMLINK"):
        offload._reject_special_git_modes(repo, base, ["src/link"])


def test_codex_review_standalone_new_mode_executable_is_rejected(case):
    """Changing mode by a standalone 'new mode' line must fail at parser boundary."""
    _, _, repo, _, _ = case
    patch = patch_for(repo)
    spoofed = patch.replace(
        b"index ", b"new mode 100755\nindex ", 1,
    )
    with pytest.raises(offload.OffloadRejected, match="UNSUPPORTED_DESTRUCTIVE"):
        offload.changed_paths(spoofed)


def test_valid_git_hunk_with_sql_comment_like_file_header_is_accepted(case):
    """Review regression: --- old comment inside counted hunk is source, not metadata."""
    tmp, db, repo, _, offer = case
    file = repo / "src" / "feature.py"
    file.write_text("def score():\n    return 1\n-- old comment\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=Canary", "-c", "user.email=canary@localhost",
        "commit", "-q", "-m", "add sql-like comment")
    file.write_text("def score():\n    return 1\n-- new comment\n", encoding="utf-8")
    patch = git(repo, "diff", "HEAD").stdout
    assert b"\n--- old comment\n" in patch
    assert offload.changed_paths(patch) == ["src/feature.py"]
    candidate = offload.seal_candidate(offer, patch, tmp / "staging")
    assert offload.verify_candidate(offer, candidate) == patch


def test_malformed_counted_hunk_still_fails_closed(case):
    _, _, repo, _, _ = case
    patch = patch_for(repo)
    forged = patch.replace(b"@@ -1,2 +1,2 @@", b"@@ -1,99 +1,2 @@")
    with pytest.raises(offload.OffloadRejected, match="PATCH_CHANGED_PATHS_UNVERIFIED"):
        offload.changed_paths(forged)
