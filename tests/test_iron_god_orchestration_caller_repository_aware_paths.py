"""Proves the orchestration caller's task-construction fix (2026-10-18,
owner-reported integration defect): _build_task_for_slice() now
delegates entirely to the one canonical
accepted_event_dispatch_coordinator.build_expected_task, instead of
maintaining its own independent (and, as deployed, subtly different --
", "-joined forbidden/acceptance instead of "; ", no .strip() on the
objective) prompt-construction logic. This exercises the REAL
Knowledge Engine Core handoff shape through the real task-construction
path, proves the default src/ layout remains unchanged, proves the
orchestration caller and the coordinator now produce byte-identical
tasks from the same packet, and proves an invalid handoff never leaves
behind a real `git worktree add` -- path validation runs strictly
before worktree provisioning.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# This proof exercises an external, machine-installed IronGodLocal script.
# GitHub-hosted runners do not contain Azathoth's live controller sources.
_EXTERNAL_IRON_GOD_SCRIPT = Path(
    r"C:\Users\jerem\AppData\Local\ExponentialEngine\IronGodLocal\accepted_event_dispatch_coordinator.py"
)
if not _EXTERNAL_IRON_GOD_SCRIPT.is_file():
    pytest.skip(
        "External IronGodLocal runtime source not installed on this runner",
        allow_module_level=True,
    )

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
        "schema_version": 1,
        "slice_id": "IG-DEFAULT",
        "state": "ELIGIBLE",
        "allowed_paths": ["src/proof.py", "tests/test_proof.py"],
        "repository": DEFAULT_REPOSITORY,
        "exact_base_sha": "a" * 40,
        "branch": "iron-god/default-slice",
        "worktree_name": "wt-default",
        "objective": "proof objective",
        "forbidden": [],
        "acceptance": [],
    }
    task = _build_task_for_slice(
        packet, slice_id="IG-DEFAULT", accepted_sha="a" * 40, worktree_root=tmp_path
    )
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
        "forbidden": ["modifying knowledge_engine/search.py", "network access"],
        "acceptance": ["pytest passes", "mypy passes"],
        "repository": KE_CORE_REPOSITORY,
        "requires_milestones": [],
    }
    task = _build_task_for_slice(
        packet,
        slice_id="KE-CORE-S1-HYBRID-SEARCH",
        accepted_sha="f266af92eeafc7ac71dfa35dc96864aeab9a6be0",
        worktree_root=tmp_path,
    )
    assert task["id"] == "KE-CORE-S1-HYBRID-SEARCH"
    assert task["source"] == "knowledge_engine/hybrid_search.py"
    assert task["tests"] == "tests/test_hybrid_search.py"
    assert task["branch"] == "iron-god/ke-core-s1-hybrid-search"
    assert task["base_sha"] == "f266af92eeafc7ac71dfa35dc96864aeab9a6be0"
    assert task["work"] == tmp_path / "ke-core-s1-hybrid-search-worker-1"
    # The specific defect this session's owner caught: forbidden/acceptance
    # must be semicolon-joined (matching the coordinator), not comma-joined.
    assert "modifying knowledge_engine/search.py; network access" in task["prompt"]
    assert "pytest passes; mypy passes" in task["prompt"]


def test_orchestration_caller_and_coordinator_produce_identical_task(tmp_path):
    """Direct proof that the two callers now produce byte-identical
    tasks from the same packet -- the exact property the owner's
    integration review demanded ("These two implementations must
    produce exactly identical task identities")."""
    from accepted_event_dispatch_coordinator import build_expected_task

    packet = {
        "schema_version": 1,
        "slice_id": "KE-CORE-S1-HYBRID-SEARCH",
        "state": "ELIGIBLE",
        "worktree_name": "ke-core-s1-hybrid-search-worker-1",
        "branch": "iron-god/ke-core-s1-hybrid-search",
        "exact_base_sha": "f266af92eeafc7ac71dfa35dc96864aeab9a6be0",
        "objective": "  Implement knowledge_engine/hybrid_search.py.  ",
        "allowed_paths": [
            "knowledge_engine/hybrid_search.py",
            "tests/test_hybrid_search.py",
        ],
        "forbidden": ["a", "b", "c"],
        "acceptance": ["x", "y", "z"],
        "repository": KE_CORE_REPOSITORY,
        "requires_milestones": [],
    }
    from_orchestrator = _build_task_for_slice(
        packet,
        slice_id="KE-CORE-S1-HYBRID-SEARCH",
        accepted_sha="f266af92eeafc7ac71dfa35dc96864aeab9a6be0",
        worktree_root=tmp_path,
    )
    from_coordinator = build_expected_task(
        slice_id="KE-CORE-S1-HYBRID-SEARCH",
        accepted_sha="f266af92eeafc7ac71dfa35dc96864aeab9a6be0",
        packet=packet,
        worktree_root=tmp_path,
    )
    assert from_orchestrator == from_coordinator


def test_orchestrator_built_task_passes_coordinator_identity_check(tmp_path):
    """Non-executing integration check (no dispatch, no claim, no
    worker): a task built via the orchestration caller's own
    _build_task_for_slice must pass the coordinator's real
    _require_exact_handoff_task unmodified -- proving the two layers
    agree without running anything."""
    from accepted_event_dispatch_coordinator import _require_exact_handoff_task

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
        "forbidden": ["modifying knowledge_engine/search.py", "network access"],
        "acceptance": ["pytest passes", "mypy passes"],
        "repository": KE_CORE_REPOSITORY,
        "requires_milestones": [],
    }
    task = _build_task_for_slice(
        packet,
        slice_id="KE-CORE-S1-HYBRID-SEARCH",
        accepted_sha="f266af92eeafc7ac71dfa35dc96864aeab9a6be0",
        worktree_root=tmp_path,
    )
    _require_exact_handoff_task(
        slice_id="KE-CORE-S1-HYBRID-SEARCH",
        accepted_sha="f266af92eeafc7ac71dfa35dc96864aeab9a6be0",
        handoffs={"KE-CORE-S1-HYBRID-SEARCH": packet},
        task=task,
        worktree_root=tmp_path,
    )  # must not raise


def test_build_task_ke_core_wrong_src_prefix_raises(tmp_path):
    packet = {
        "schema_version": 1,
        "slice_id": "IG-KE-WRONG",
        "state": "ELIGIBLE",
        "allowed_paths": ["src/hybrid_search.py", "tests/test_hybrid_search.py"],
        "repository": KE_CORE_REPOSITORY,
        "exact_base_sha": "a" * 40,
        "branch": "iron-god/ke-wrong",
        "worktree_name": "wt-ke-wrong",
        "objective": "x",
        "forbidden": [],
        "acceptance": [],
    }
    with pytest.raises(RuntimeError, match="DISPATCH_HANDOFF_TASK_IDENTITY_MISMATCH"):
        _build_task_for_slice(
            packet,
            slice_id="IG-KE-WRONG",
            accepted_sha="a" * 40,
            worktree_root=tmp_path,
        )


@pytest.mark.parametrize(
    "mutate_field,new_value",
    [
        ("objective", "a different objective entirely"),
        (
            "allowed_paths",
            ["knowledge_engine/different.py", "tests/test_hybrid_search.py"],
        ),
        ("forbidden", ["something else"]),
        ("acceptance", ["something else"]),
    ],
)
def test_mutated_handoff_field_changes_task_identity(tmp_path, mutate_field, new_value):
    """Owner requirement: 'Verify that changing the objective, allowed
    paths, forbidden paths, or acceptance criteria still causes
    identity verification to fail' -- i.e. the task actually constructed
    from a MUTATED packet must differ from the one constructed from the
    original, so a caller comparing against a stale/wrong task is still
    caught."""
    base_packet = {
        "schema_version": 1,
        "slice_id": "IG-MUTATE",
        "state": "ELIGIBLE",
        "worktree_name": "wt-mutate",
        "branch": "iron-god/mutate-slice",
        "exact_base_sha": "a" * 40,
        "objective": "original objective",
        "allowed_paths": ["knowledge_engine/original.py", "tests/test_original.py"],
        "forbidden": ["original forbidden"],
        "acceptance": ["original acceptance"],
        "repository": KE_CORE_REPOSITORY,
    }
    original_task = _build_task_for_slice(
        base_packet, slice_id="IG-MUTATE", accepted_sha="a" * 40, worktree_root=tmp_path
    )
    mutated_packet = {**base_packet, mutate_field: new_value}
    mutated_task = _build_task_for_slice(
        mutated_packet,
        slice_id="IG-MUTATE",
        accepted_sha="a" * 40,
        worktree_root=tmp_path,
    )
    assert original_task != mutated_task
    # And the coordinator's own identity check must reject the stale task
    # against the mutated packet's handoff.
    from accepted_event_dispatch_coordinator import _require_exact_handoff_task

    with pytest.raises(RuntimeError, match="DISPATCH_HANDOFF_TASK_IDENTITY_MISMATCH"):
        _require_exact_handoff_task(
            slice_id="IG-MUTATE",
            accepted_sha="a" * 40,
            handoffs={"IG-MUTATE": mutated_packet},
            task=original_task,
            worktree_root=tmp_path,
        )


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
        OrchestrationBlockedError, match="DISPATCH_HANDOFF_TASK_IDENTITY_MISMATCH"
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
            authorized_repositories=frozenset({KE_CORE_REPOSITORY}),
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
