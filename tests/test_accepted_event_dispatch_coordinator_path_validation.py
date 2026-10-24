"""Proves the real, in-place dispatch-validator path-convention fix in
accepted_event_dispatch_coordinator.py (2026-10-18), per the owner's
directive 1: "Generalize the dispatch validator so Iron God supports
different legitimate Python repository layouts... Test this against
both the project-orchestrator and Knowledge Engine Core layouts."

Loads the real, edited coordinator module directly from its real,
on-disk, shared IronGodLocal path via importlib -- the same proven
pattern this session already uses for local_continuous_worker.py --
so this exercises the actual deployed fix, not a disconnected copy.
Targets _require_exact_handoff_task directly: it is the one function
responsible for the path-shape decision, and every other function in
this module (dispatch_one_accepted_event, claim sequencing) is already
covered by the existing, unmodified test_accepted_event_dispatch_*.py
suite in IronGodLocal, which this change does not touch.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

from iron_god_local_test_support import IRON_GOD_LOCAL_DIR, requires_iron_god_local

pytestmark = requires_iron_god_local("accepted_event_dispatch_coordinator.py")

COORDINATOR_PATH = IRON_GOD_LOCAL_DIR / "accepted_event_dispatch_coordinator.py"


@pytest.fixture()
def coordinator():
    spec = importlib.util.spec_from_file_location(
        "path_validation_proof_coordinator", COORDINATOR_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _packet(
    *, slice_id, base_sha, worktree_name, branch, allowed_paths, repository
) -> dict:
    return {
        "schema_version": 1,
        "slice_id": slice_id,
        "state": "ELIGIBLE",
        "worktree_name": worktree_name,
        "branch": branch,
        "exact_base_sha": base_sha,
        "objective": "proof objective",
        "allowed_paths": allowed_paths,
        "forbidden": [],
        "acceptance": [],
        "repository": repository,
    }


def _task_for(packet: dict, *, work: Path) -> dict:
    source = next(p for p in packet["allowed_paths"] if not p.startswith("tests/"))
    tests = next(p for p in packet["allowed_paths"] if p.startswith("tests/"))
    prompt = (
        "Objective: "
        + packet["objective"].strip()
        + " Allowed paths: "
        + ", ".join(packet["allowed_paths"])
        + ". Forbidden: . Acceptance: "
    )
    return {
        "id": packet["slice_id"],
        "base_sha": packet["exact_base_sha"],
        "branch": packet["branch"],
        "work": work,
        "source": source,
        "tests": tests,
        "prompt": prompt,
    }


def test_backward_compatible_default_src_layout_unchanged(coordinator, tmp_path):
    """An unlisted repository must keep requiring the literal 'src/'
    prefix exactly as before -- the fix must not loosen the default."""
    packet = _packet(
        slice_id="IG-DEFAULT",
        base_sha="a" * 40,
        worktree_name="wt-default",
        branch="iron-god/default-slice",
        allowed_paths=["src/proof.py", "tests/test_proof.py"],
        repository="test-proof-owner/test-proof-repo",
    )
    work = tmp_path / "wt-default"
    work.mkdir()
    task = _task_for(packet, work=work)
    coordinator._require_exact_handoff_task(
        slice_id="IG-DEFAULT",
        accepted_sha="a" * 40,
        handoffs={"IG-DEFAULT": packet},
        task=task,
        worktree_root=tmp_path,
    )  # must not raise


def test_default_layout_rejects_non_src_prefix(coordinator, tmp_path):
    """An unlisted repository must still reject a non-'src/' implementation
    path -- confirms the default truly did not change to 'anything goes'."""
    packet = _packet(
        slice_id="IG-DEFAULT",
        base_sha="a" * 40,
        worktree_name="wt-default",
        branch="iron-god/default-slice",
        allowed_paths=["lib/proof.py", "tests/test_proof.py"],
        repository="test-proof-owner/test-proof-repo",
    )
    work = tmp_path / "wt-default"
    work.mkdir()
    task = _task_for(packet, work=work)
    with pytest.raises(RuntimeError, match="DISPATCH_HANDOFF_TASK_IDENTITY_MISMATCH"):
        coordinator._require_exact_handoff_task(
            slice_id="IG-DEFAULT",
            accepted_sha="a" * 40,
            handoffs={"IG-DEFAULT": packet},
            task=task,
            worktree_root=tmp_path,
        )


def test_knowledge_engine_core_layout_is_accepted(coordinator, tmp_path):
    """The real Knowledge Engine Core layout (knowledge_engine/, no src/)
    must now validate successfully for its own real repository identifier."""
    packet = _packet(
        slice_id="IG-KE-CORE",
        base_sha="b" * 40,
        worktree_name="wt-ke-core",
        branch="iron-god/ke-core-slice",
        allowed_paths=[
            "knowledge_engine/hybrid_search.py",
            "tests/test_hybrid_search.py",
        ],
        repository="jweter/knowledge-engine-core",
    )
    work = tmp_path / "wt-ke-core"
    work.mkdir()
    task = _task_for(packet, work=work)
    coordinator._require_exact_handoff_task(
        slice_id="IG-KE-CORE",
        accepted_sha="b" * 40,
        handoffs={"IG-KE-CORE": packet},
        task=task,
        worktree_root=tmp_path,
    )  # must not raise


def test_knowledge_engine_core_rejects_mismatched_src_prefix(coordinator, tmp_path):
    """A packet declaring repository=knowledge-engine-core but using the
    'src/' prefix (the WRONG layout for that specific repository) must be
    rejected -- the mapping is enforced, not merely permissive."""
    packet = _packet(
        slice_id="IG-KE-CORE",
        base_sha="b" * 40,
        worktree_name="wt-ke-core",
        branch="iron-god/ke-core-slice",
        allowed_paths=["src/hybrid_search.py", "tests/test_hybrid_search.py"],
        repository="jweter/knowledge-engine-core",
    )
    work = tmp_path / "wt-ke-core"
    work.mkdir()
    task = _task_for(packet, work=work)
    with pytest.raises(RuntimeError, match="DISPATCH_HANDOFF_TASK_IDENTITY_MISMATCH"):
        coordinator._require_exact_handoff_task(
            slice_id="IG-KE-CORE",
            accepted_sha="b" * 40,
            handoffs={"IG-KE-CORE": packet},
            task=task,
            worktree_root=tmp_path,
        )


def test_missing_repository_field_is_rejected(coordinator, tmp_path):
    packet = _packet(
        slice_id="IG-NOREPO",
        base_sha="c" * 40,
        worktree_name="wt-norepo",
        branch="iron-god/norepo-slice",
        allowed_paths=["src/proof.py", "tests/test_proof.py"],
        repository="",
    )
    work = tmp_path / "wt-norepo"
    work.mkdir()
    task = _task_for(packet, work=work)
    with pytest.raises(RuntimeError, match="DISPATCH_HANDOFF_TASK_IDENTITY_MISMATCH"):
        coordinator._require_exact_handoff_task(
            slice_id="IG-NOREPO",
            accepted_sha="c" * 40,
            handoffs={"IG-NOREPO": packet},
            task=task,
            worktree_root=tmp_path,
        )


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="symlink support required")
def test_symlink_escape_is_rejected(coordinator, tmp_path):
    """A declared implementation path that LOOKS innocent in text but
    resolves, via a real symlink planted inside the worktree, to a
    location outside the worktree root must be rejected."""
    outside = tmp_path / "outside-the-worktree"
    outside.mkdir()
    (outside / "escaped.py").write_text("x = 1\n", encoding="utf-8")

    work = tmp_path / "wt-escape"
    work.mkdir()
    (work / "tests").mkdir()
    (work / "tests" / "test_proof.py").write_text(
        "def test_x():\n    pass\n", encoding="utf-8"
    )

    try:
        (work / "src").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("creating a symlink is not permitted in this environment")

    packet = _packet(
        slice_id="IG-ESCAPE",
        base_sha="d" * 40,
        worktree_name="wt-escape",
        branch="iron-god/escape-slice",
        allowed_paths=["src/escaped.py", "tests/test_proof.py"],
        repository="test-proof-owner/test-proof-repo",
    )
    task = _task_for(packet, work=work)
    with pytest.raises(RuntimeError, match="DISPATCH_HANDOFF_TASK_IDENTITY_MISMATCH"):
        coordinator._require_exact_handoff_task(
            slice_id="IG-ESCAPE",
            accepted_sha="d" * 40,
            handoffs={"IG-ESCAPE": packet},
            task=task,
            worktree_root=tmp_path,
        )


def test_real_knowledge_engine_web_and_ai_layouts_also_accepted(coordinator, tmp_path):
    for repo, prefix in (
        ("jweter/knowledge-engine-ai", "knowledge_engine_ai/"),
        ("jweter/knowledge-engine-web", "knowledge_engine_web/"),
    ):
        packet = _packet(
            slice_id="IG-SIB",
            base_sha="e" * 40,
            worktree_name="wt-sib",
            branch="iron-god/sib-slice",
            allowed_paths=[prefix + "module.py", "tests/test_module.py"],
            repository=repo,
        )
        work = tmp_path / "wt-sib"
        work.mkdir(exist_ok=True)
        task = _task_for(packet, work=work)
        coordinator._require_exact_handoff_task(
            slice_id="IG-SIB",
            accepted_sha="e" * 40,
            handoffs={"IG-SIB": packet},
            task=task,
            worktree_root=tmp_path,
        )  # must not raise
        (work / prefix.rstrip("/")).rmdir() if (
            work / prefix.rstrip("/")
        ).is_dir() else None
