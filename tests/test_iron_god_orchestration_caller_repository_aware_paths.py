"""Proves the orchestration caller's own _build_task_for_slice() fix
(2026-10-18, owner-reviewer finding): it duplicated the exact
"src/"-hardcoding bug already fixed in
accepted_event_dispatch_coordinator.py and local_continuous_worker.py.
This exercises the REAL Knowledge Engine Core handoff shape through
the real task-construction path, proves the default src/ layout
remains unchanged, and proves an invalid handoff never leaves behind
a real `git worktree add` -- path validation now runs strictly before
worktree provisioning.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# accepted_event_dispatch_coordinator.py is a bare module living in
# IronGodLocal, not part of the installed project_orchestrator package --
# _build_task_for_slice imports it by bare name, matching how it is
# actually deployed. Make it importable the same way every other proof
# test this session does.
_IRON_GOD_LOCAL = Path(r"C:\Users\jerem\AppData\Local\ExponentialEngine\IronGodLocal")
if str(_IRON_GOD_LOCAL) not in sys.path:
    sys.path.insert(0, str(_IRON_GOD_LOCAL))

from project_orchestrator.iron_god_orchestration_caller import (
    OrchestrationBlockedError,
    _build_task_for_slice,
    provision_candidate_worktree,
    run_continuous_development_cycle,
)

DEFAULT_REPOSITORY = "test-proof-owner/test-proof-repo"
KE_CORE_REPOSITORY = "jweter/knowledge-engine-core"


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


def test_build_task_default_src_layout_unchanged(tmp_path):
    packet = {
        "slice_id": "IG-DEFAULT",
        "allowed_paths": ["src/proof.py", "tests/test_proof.py"],
        "repository": DEFAULT_REPOSITORY,
        "exact_base_sha": "a" * 40,
        "branch": "iron-god/default-slice",
        "worktree_name": "wt-default",
        "objective": "proof objective",
        "forbidden": [],
        "acceptance": [],
    }
    task = _build_task_for_slice(packet, worktree_root=tmp_path)
    assert task["source"] == "src/proof.py"
    assert task["tests"] == "tests/test_proof.py"
    assert task["work"] == tmp_path / "wt-default"


def test_build_task_real_knowledge_engine_core_handoff(tmp_path):
    """The exact real handoff shape placed in
    handoffs/KE-CORE-S1-HYBRID-SEARCH.json -- proves it passes task
    construction through the real orchestration caller path."""
    packet = {
        "schema_version": 1,
        "slice_id": "KE-CORE-S1-HYBRID-SEARCH",
        "state": "ELIGIBLE",
        "worktree_name": "ke-core-s1-hybrid-search-worker-1",
        "branch": "iron-god/ke-core-s1-hybrid-search",
        "exact_base_sha": "f266af92eeafc7ac71dfa35dc96864aeab9a6be0",
        "objective": "Implement knowledge_engine/hybrid_search.py.",
        "allowed_paths": [
            "knowledge_engine/hybrid_search.py",
            "tests/test_hybrid_search.py",
        ],
        "forbidden": ["modifying knowledge_engine/search.py"],
        "acceptance": ["pytest passes"],
        "repository": KE_CORE_REPOSITORY,
        "requires_milestones": [],
    }
    task = _build_task_for_slice(packet, worktree_root=tmp_path)
    assert task["id"] == "KE-CORE-S1-HYBRID-SEARCH"
    assert task["source"] == "knowledge_engine/hybrid_search.py"
    assert task["tests"] == "tests/test_hybrid_search.py"
    assert task["branch"] == "iron-god/ke-core-s1-hybrid-search"
    assert task["base_sha"] == "f266af92eeafc7ac71dfa35dc96864aeab9a6be0"
    assert task["work"] == tmp_path / "ke-core-s1-hybrid-search-worker-1"


def test_build_task_ke_core_wrong_src_prefix_raises_clean_error(tmp_path):
    packet = {
        "slice_id": "IG-KE-WRONG",
        "allowed_paths": ["src/hybrid_search.py", "tests/test_hybrid_search.py"],
        "repository": KE_CORE_REPOSITORY,
        "exact_base_sha": "a" * 40,
        "branch": "iron-god/ke-wrong",
        "worktree_name": "wt-ke-wrong",
        "objective": "x",
        "forbidden": [],
        "acceptance": [],
    }
    with pytest.raises(RuntimeError, match="HANDOFF_PATH_SHAPE_UNSUPPORTED"):
        _build_task_for_slice(packet, worktree_root=tmp_path)


def test_invalid_handoff_never_creates_a_worktree(tmp_path, monkeypatch):
    """The ordering fix: path validation must run BEFORE
    provision_candidate_worktree, so an invalid handoff cannot leave
    behind a real Git worktree with nothing to build in it."""
    repo = _init_repo(tmp_path / "repo")
    base_sha = _git(repo, "rev-parse", "HEAD")

    provision_calls = []
    import project_orchestrator.iron_god_orchestration_caller as caller_module

    def spy_provision(*args, **kwargs):
        provision_calls.append(kwargs.get("worktree_name"))
        return provision_candidate_worktree(*args, **kwargs)

    monkeypatch.setattr(caller_module, "provision_candidate_worktree", spy_provision)

    from project_orchestrator.local_first_state import LocalFirstState
    from project_orchestrator.local_git_refs import LocalGitRefs

    flow = tmp_path / "flow-state"
    flow.mkdir()
    (flow / "human-mode.json").write_text('{"enabled": false}', encoding="utf-8")
    refs = LocalGitRefs(repo, owner_state_root=flow)
    refs.bootstrap_accepted(base_sha)
    refs.protect("milestone", "M-0", base_sha)
    state = LocalFirstState(tmp_path / "accepted.sqlite")
    state.prepare_acceptance(
        repository=KE_CORE_REPOSITORY,
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

    # Invalid handoff: declares the KE-core repository but uses the WRONG
    # (src/) prefix for its implementation path.
    bad_packet = {
        "schema_version": 1,
        "slice_id": "IG-BAD",
        "state": "ELIGIBLE",
        "worktree_name": "wt-bad",
        "branch": "iron-god/bad-slice",
        "exact_base_sha": base_sha,
        "objective": "x",
        "allowed_paths": ["src/wrong.py", "tests/test_wrong.py"],
        "forbidden": [],
        "acceptance": [],
        "repository": KE_CORE_REPOSITORY,
        "requires_milestones": [],
    }
    controller = {
        "mode": "candidate_mode",
        "candidate_execution_authorized": True,
        "slices": [{"id": "IG-BAD", "state": "ELIGIBLE"}],
    }

    def dispatch_fn(**kwargs):
        pytest.fail("dispatch_fn must never be reached for an invalid handoff")

    with pytest.raises(
        OrchestrationBlockedError, match="HANDOFF_PATH_SHAPE_UNSUPPORTED"
    ):
        run_continuous_development_cycle(
            orchestration_authorized=True,
            dispatch_fn=dispatch_fn,
            worker_executor=lambda **kw: pytest.fail("must not execute"),
            state=state,
            state_db_path=tmp_path / "accepted.sqlite",
            refs=refs,
            repo=repo,
            repository=KE_CORE_REPOSITORY,
            owner_state_root=flow,
            controller=controller,
            handoffs={"IG-BAD": bad_packet},
            claims_path=tmp_path / "claims.sqlite",
            lease_path=tmp_path / "leases.sqlite",
            worktree_root=tmp_path,
            next_milestone_id_fn=lambda i: f"M-{i + 1}",
            product_reality_adapter=None,
            product_reality_required=False,
            max_cycles=1,
        )

    assert provision_calls == [], (
        "provision_candidate_worktree must never be called for an invalid handoff"
    )
    assert not (tmp_path / "wt-bad").exists(), (
        "no real worktree must exist on disk for an invalid handoff"
    )
    assert _git(repo, "worktree", "list").count("\n") == 0, (
        "the repo must have no additional worktrees registered"
    )
