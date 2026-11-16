"""Exact-head, owner-gated, durable local advice; never work authorization."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from project_orchestrator import iron_god_microtask_progress as progress
from project_orchestrator import iron_god_microtask_repair_advisor as advisor
from project_orchestrator import iron_god_task_decomposer as decomposer
from project_orchestrator.state_io import atomic_json

PARENT = "RM-web-cap-feature-00001"
REPO = "jweter/knowledge-engine-web"
HEAD = "c" * 40
GOOD = {
    "steps": [
        {
            "key": "s1",
            "action": "change",
            "target": "pkg/one.py",
            "objective": "Implement focused title normalization correction",
            "verification": "Assert original title behavior with targeted tests",
            "criteria": [1],
            "depends_on": [],
        }
    ]
}


@pytest.fixture
def lane(tmp_path: Path) -> Path:
    plan = {
        "schema_version": 1,
        "parent_id": PARENT,
        "repository": REPO,
        "status": "BLOCKED",
        "planned_at_head": "a" * 40,
        "roadmap_ref": "engineering/roadmap.json#1",
        "allowed_paths": ["pkg/**", "tests/**"],
        "forbidden_paths": ["pkg/secret/**"],
        "slice_acceptance": ["normalize title", "report title stats"],
        "coverage": {"1": ["t1"], "2": ["t2"]},
        "subtasks": [
            {
                "key": "t1",
                "module_path": "pkg/one.py",
                "test_path": "tests/test_one.py",
                "depends_on": [],
            },
            {"key": "t2", "module_path": "pkg/two.py", "depends_on": ["t1"]},
            {"key": "t3", "module_path": "pkg/sibling.py", "depends_on": []},
        ],
    }
    candidate = {
        "kind": "EXHAUSTED_LOCAL_MICROTASK",
        "failed_key": "t1",
        "blocked_keys": ["t1", "t2"],
        "already_completed_keys": ["t3"],
        "affected_owner_criteria": [
            {"index": 1, "text": "normalize title"},
            {"index": 2, "text": "report title stats"},
        ],
        "narrow_existing_paths": ["pkg/one.py", "tests/test_one.py"],
        "observed_attempt_id": PARENT + "-t1-r1",
        "outcome": "FAIL: SLICE_TESTS_FAILED",
        "attempts": 3,
        "packet_ids": ["PRODUCT-FAILED"],
        "requires_new_authorization": True,
        "dispatchable": False,
        "attempt_budget_reset": False,
        "proposed_action": "PROPOSE_SMALLER_ROADMAP_REPAIR_FOR_OWNER_APPROVAL",
    }
    checkpoint = {
        "advisory_only": True,
        "plan_status": "BLOCKED",
        "parent_id": PARENT,
        "repository": REPO,
        "observed_repository_head": HEAD,
        "subtasks": [
            {
                "key": "t1",
                "status": "EXHAUSTED",
                "attempt_id": PARENT + "-t1-r1",
                "outcome": "FAIL: SLICE_TESTS_FAILED",
                "attempts": 3,
                "packet_ids": ["PRODUCT-FAILED"],
            }
        ],
        "repair_candidates": [candidate],
    }
    atomic_json(decomposer.plan_path(tmp_path, PARENT), plan)
    atomic_json(progress.checkpoint_path(tmp_path, plan), checkpoint)
    return tmp_path


def do(
    lane: Path, *, authority=lambda: True, ask_local=lambda _prompt: GOOD, **overrides
):
    return advisor.propose_current(
        lane,
        overrides.get("parent_id", PARENT),
        overrides.get("repository", REPO),
        overrides.get("head", HEAD),
        overrides.get("failed_key", "t1"),
        authority=authority,
        ask_local=ask_local,
        model_provenance={
            "provider": "LOCAL_OLLAMA_ONLY",
            "model_used": "qwen2.5-coder:7b",
        },
    )


def test_persists_one_authorized_advisory_and_replays_without_model_call(
    lane: Path,
) -> None:
    calls = []
    first = do(lane, ask_local=lambda prompt: (calls.append(prompt), deepcopy(GOOD))[1])
    assert first["status"] == "ADVISORY_RECORDED"
    rec = json.loads(Path(first["path"]).read_text(encoding="utf-8"))
    assert rec["advisory_only"] is True
    assert rec["dispatchable"] is False
    assert rec["requires_new_authorization"] is True
    assert rec["attempt_budget_reset"] is False
    assert rec["product_acceptance"] == "UNVERIFIED"
    assert rec["outcome"]["ok"] is True
    assert rec["outcome"]["proposal"]["steps"][0]["target"] == "pkg/one.py"
    assert rec["model_provenance"]["model_used"] == "qwen2.5-coder:7b"
    assert len(calls) == 1

    second = do(lane, ask_local=lambda _: pytest.fail("duplicate model invocation"))
    assert second["status"] == "ALREADY_RECORDED"
    assert second["path"] == first["path"]
    assert len(calls) == 1


def test_owner_deny_prevents_model_and_record(lane: Path) -> None:
    result = do(
        lane,
        authority=lambda: False,
        ask_local=lambda _: pytest.fail("unauthorized model"),
    )
    assert result == {"status": "OWNER_AUTHORITY_DENIED"}
    assert not (lane / advisor.ADVICE_DIR).exists()


def test_owner_revocation_during_local_model_drops_unpublished_result(
    lane: Path,
) -> None:
    permitted = [True]

    def call(_: str):
        permitted[0] = False
        return deepcopy(GOOD)

    result = do(lane, authority=lambda: permitted[0], ask_local=call)
    assert result == {"status": "OWNER_AUTHORITY_DENIED_AFTER_MODEL"}
    assert not list((lane / advisor.ADVICE_DIR).glob("*.json"))
    assert not list((lane / advisor.ADVICE_DIR).glob("*.lock"))


def test_checkpoint_change_during_model_invalidates_result(lane: Path) -> None:
    plan = json.loads(decomposer.plan_path(lane, PARENT).read_text(encoding="utf-8"))
    path = progress.checkpoint_path(lane, plan)

    def call(_: str):
        source = json.loads(path.read_text(encoding="utf-8"))
        source["checkpointed_at_utc"] = "state was recomputed while model planned"
        atomic_json(path, source)
        return deepcopy(GOOD)

    assert do(lane, ask_local=call) == {"status": "SOURCE_OR_CHECKPOINT_CHANGED"}
    assert not list((lane / advisor.ADVICE_DIR).glob("*.json"))


def test_checkpoint_revision_change_requires_a_new_advice_record(lane: Path) -> None:
    first = do(lane)
    plan = json.loads(decomposer.plan_path(lane, PARENT).read_text(encoding="utf-8"))
    path = progress.checkpoint_path(lane, plan)
    source = json.loads(path.read_text(encoding="utf-8"))
    source["receipts"] = [{"key": "t3", "receipt": "GITHUB_MERGED_IN_LANE_LEDGER"}]
    atomic_json(path, source)
    second = do(lane)
    assert second["status"] == "ADVISORY_RECORDED"
    assert first["path"] != second["path"]


def test_exclusive_lock_blocks_duplicate_or_crash_replay(lane: Path) -> None:
    source = advisor._load_current(lane, PARENT, REPO, HEAD, "t1")
    assert source
    plan, checkpoint, candidate, *_ = source
    path = advisor.proposal_path(lane, plan, candidate, HEAD, checkpoint)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(".lock")
    lock.write_text("prior planner may have crashed", encoding="utf-8")
    assert do(lane, ask_local=lambda _: pytest.fail("duplicate model")) == {
        "status": "ADVICE_LOCK_HELD_RECONCILE_MANUALLY"
    }
    assert lock.exists()  # a stale lock is NOT silently stolen


def test_wrong_head_repo_parent_or_nonexhausted_child_never_asks(lane: Path) -> None:
    for override in (
        {"head": "d" * 40},
        {"head": "short"},
        {"repository": "jweter/VoiceStudio"},
        {"parent_id": "../../unsafe"},
        {"failed_key": "t2"},
    ):
        assert do(lane, ask_local=lambda _: pytest.fail("not current"), **override)[
            "status"
        ] == ("NO_CURRENT_EXHAUSTED_CANDIDATE")


def test_tampered_child_outcome_fails_closed(lane: Path) -> None:
    plan = json.loads(decomposer.plan_path(lane, PARENT).read_text(encoding="utf-8"))
    path = progress.checkpoint_path(lane, plan)
    source = json.loads(path.read_text(encoding="utf-8"))
    source["repair_candidates"][0]["attempts"] = 0
    atomic_json(path, source)
    assert do(lane, ask_local=lambda _: pytest.fail("unverified")) == {
        "status": "NO_CURRENT_EXHAUSTED_CANDIDATE"
    }


def test_model_unavailable_is_not_a_permanent_retry_blacklist(lane: Path) -> None:
    def down(_: str):
        raise TimeoutError("memory gate or local Ollama unavailable")

    assert do(lane, ask_local=down)["status"] == "LOCAL_MODEL_UNAVAILABLE"
    assert not list((lane / advisor.ADVICE_DIR).glob("*.json"))
    assert not list((lane / advisor.ADVICE_DIR).glob("*.lock"))
    assert do(lane)["status"] == "ADVISORY_RECORDED"


def test_two_rejected_local_proposals_are_bounded_and_recorded(lane: Path) -> None:
    calls = []
    result = do(lane, ask_local=lambda prompt: (calls.append(prompt), {"steps": []})[1])
    assert result["status"] == "ADVISORY_RECORDED"
    assert len(calls) == 2
    assert "REPAIR_STEPS_MUST_BE_1_TO_3" in calls[1]
    assert result["record"]["outcome"]["ok"] is False
    assert result["record"]["outcome"]["reason"] == "REPAIR_PROPOSAL_REJECTED"
    assert (
        do(lane, ask_local=lambda _: pytest.fail("no infinite retries"))["status"]
        == "ALREADY_RECORDED"
    )


def test_cli_requires_supervisor_acknowledged_owner_grant_and_exact_clone_head(
    lane: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts import iron_god_repair_advisor as cli
    from project_orchestrator import execution_authority as owner_auth
    from project_orchestrator import iron_god_local_model as local_model

    monkeypatch.setenv("IRON_GOD_ACCEPTED_SHA", "e" * 40)
    monkeypatch.setattr(
        local_model,
        "guarded_generate",
        lambda *_a, **_kw: pytest.fail("unapproved model call"),
    )
    args = [
        "--lane",
        str(lane),
        "--clone",
        str(lane),
        "--parent",
        PARENT,
        "--repository",
        REPO,
        "--head",
        HEAD,
        "--failed-key",
        "t1",
    ]
    monkeypatch.setattr(
        owner_auth,
        "evaluate_execution_authority",
        lambda **_kw: SimpleNamespace(permitted=False),
    )
    assert cli.main(args) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "OWNER_AUTHORITY_DENIED"

    monkeypatch.setattr(
        owner_auth,
        "evaluate_execution_authority",
        lambda **_kw: SimpleNamespace(permitted=True),
    )
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *_a, **_kw: SimpleNamespace(stdout="f" * 40),
    )
    assert cli.main(args) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "OWNER_AUTHORITY_DENIED"
    assert not (lane / advisor.ADVICE_DIR).exists()


def test_actual_checkpoint_generator_feeds_owner_gated_advisor(lane: Path) -> None:
    """End-to-end read-only bridge from real #717 candidates, not a hand-built JSON fixture."""
    # The shared fixture intentionally plants an old, incomplete hand-authored
    # checkpoint for advisory-reader tests. Production must preserve that
    # unverified history rather than overwrite it. This test instead exercises
    # canonical checkpoint creation from a NEW plan with no prior record.
    plan = json.loads(decomposer.plan_path(lane, PARENT).read_text(encoding="utf-8"))
    legacy_fixture = progress.checkpoint_path(lane, plan)
    assert legacy_fixture.is_file()
    legacy_fixture.unlink()  # Test isolation only: never delete production evidence.
    t1 = PARENT + "-t1"
    r1 = PARENT + "-t1-r1"
    t3 = PARENT + "-t3"
    items = [
        {"id": ident, "repository": REPO, "status": "approved"}
        for ident in (t1, r1, t3)
    ]
    state = {
        "items": {
            t1: {"outcome": "FAIL: SLICE_TESTS_FAILED", "attempts": 3},
            r1: {
                "outcome": "FAIL: SLICE_TESTS_FAILED",
                "attempts": 3,
                "packet_id": "PRODUCT-FAILED",
            },
            t3: {"outcome": "MERGED", "packets": ["PRODUCT-VERIFIED"]},
        }
    }
    assert progress.record_checkpoints(lane, REPO, HEAD, items, state) == [PARENT]
    plan = json.loads(decomposer.plan_path(lane, PARENT).read_text(encoding="utf-8"))
    generated = json.loads(
        progress.checkpoint_path(lane, plan).read_text(encoding="utf-8")
    )
    assert generated["repair_candidates"][0]["failed_key"] == "t1"
    assert generated["repair_candidates"][0]["blocked_keys"] == ["t1", "t2"]
    assert generated["repair_candidates"][0]["already_completed_keys"] == ["t3"]
    assert generated["subtasks"][0]["status"] == "EXHAUSTED"

    result = do(lane)
    assert result["status"] == "ADVISORY_RECORDED"
    assert result["record"]["outcome"]["proposal"]["dispatchable"] is False
    assert result["record"]["observed_attempt_id"] == r1
    assert result["record"]["checkpoint_sha256"]


def test_truncated_model_json_is_bounded_and_not_replayed_forever(lane: Path) -> None:
    calls: list[str] = []

    def malformed(prompt: str):
        calls.append(prompt)
        raise json.JSONDecodeError("truncated", '{"steps": [', 11)

    first = do(lane, ask_local=malformed)
    assert first["status"] == "ADVISORY_RECORDED"
    assert first["record"]["outcome"]["reason"] == "REPAIR_PROPOSAL_REJECTED"
    assert len(calls) == 2
    assert (
        do(lane, ask_local=lambda _: pytest.fail("repeat parsed invalid JSON"))[
            "status"
        ]
        == "ALREADY_RECORDED"
    )
