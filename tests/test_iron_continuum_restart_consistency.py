"""IRON CONTINUUM: deterministic restart/continuation proof for canonical local microtasks.

This is a unit/integration canary, not a claim of real Ollama, installed-release
or native-product acceptance. No external GitHub/Claude/cloud calls occur.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_orchestrator import iron_god_microtask_progress as checkpoints
from project_orchestrator import iron_god_task_decomposer as decomposer
from project_orchestrator.state_io import atomic_json

REPO = "jweter/knowledge-engine-web"
PARENT = "RM-knowledge-engine-web-iron-continuum-proving-12345"
HEAD = "a" * 40


def _plan(lane: Path) -> dict:
    children = []
    for n in range(1, 4):
        children.append({
            "key": f"t{n}",
            "kind": "new",
            "title": f"verified helper {n}",
            "depends_on": [f"t{n - 1}"] if n > 1 else [],
            "module_path": f"pkg/verified_helper_{n}.py",
            "function_name": f"verified_helper_{n}",
            "signature": f"def verified_helper_{n}(value: int) -> int:",
            "done_when": f"helper {n} returns the value plus {n}",
            "acceptance_examples": [
                {"call": "{fn}(1)", "expected": 1 + n, "why": "fixed example"},
                {"call": "{fn}(0)", "expected": n, "why": "fixed example"},
            ],
        })
    plan = {
        "schema_version": 1, "parent_id": PARENT,
        "repository": REPO, "roadmap_ref": "engineering/owner-roadmap.json#cap/proof",
        "capability": "cap", "slice": "proof",
        "objective": "Three dependency-ordered bounded code edits",
        "slice_acceptance": ["helper 1", "helper 2", "helper 3"],
        "allowed_paths": ["pkg/**", "tests/**"], "forbidden_paths": [],
        "planned_at_head": HEAD, "status": "ACTIVE", "oracle": "OWNER_APPROVED",
        "coverage": {"1": ["t1"], "2": ["t2"], "3": ["t3"]},
        "subtasks": children, "residual": [],
    }
    atomic_json(lane / "decompositions" / f"{PARENT}.json", plan)
    return plan


def _item(child: int) -> dict:
    return {
        "id": f"{PARENT}-t{child}", "repository": REPO,
        "status": "approved", "level": 1,
    }


def _merged_state(*children: int) -> tuple[list[dict], dict]:
    items = [_item(n) for n in children]
    state = {
        "items": {
            item["id"]: {
                "outcome": "MERGED",
                "packets": [f"PRODUCT-{n:02}"],
                "attempts": 1,
            }
            for n, item in zip(children, items, strict=True)
        }
    }
    return items, state


def _ready(lane: Path, items: list[dict], state: dict) -> list[str]:
    return [
        request["id"]
        for request in decomposer.child_requests(lane, REPO, HEAD, items, state)
    ]


def test_three_microtasks_resume_after_two_accepted_from_disk_without_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import subprocess
    import urllib.request

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Canary must use no network, cloud, or subprocess")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)

    lane = tmp_path / "lane"
    plan = _plan(lane)
    assert _ready(lane, [], {}) == [f"{PARENT}-t1"]

    items, state = _merged_state(1)
    checkpoints.record_checkpoints(lane, REPO, HEAD, items, state)
    assert _ready(lane, items, state) == [f"{PARENT}-t2"]

    items, state = _merged_state(1, 2)
    checkpoints.record_checkpoints(lane, REPO, HEAD, items, state)
    checkpoint = checkpoints.checkpoint_path(lane, plan)
    before = checkpoint.read_bytes()
    assert _ready(lane, items, state) == [f"{PARENT}-t3"]

    # Model/supervisor process interrupted after the second accepted edit.
    # A NEW Python caller starts from canonical files, not in-memory objects.
    atomic_json(lane / "canary-canonical-items.json", items)
    atomic_json(lane / "canary-canonical-state.json", state)
    restored_items = json.loads((lane / "canary-canonical-items.json").read_text())
    restored_state = json.loads((lane / "canary-canonical-state.json").read_text())
    assert _ready(lane, restored_items, restored_state) == [f"{PARENT}-t3"]
    assert checkpoints.record_checkpoints(lane, REPO, HEAD, restored_items, restored_state) == []
    assert checkpoint.read_bytes() == before  # no duplicate receipt or timestamp churn

    final_items, final_state = _merged_state(1, 2, 3)
    checkpoints.record_checkpoints(lane, REPO, HEAD, final_items, final_state)
    assert _ready(lane, final_items, final_state) == []
    data = json.loads(checkpoint.read_text())
    assert [r["status"] for r in data["subtasks"]] == ["DONE", "DONE", "DONE"]
    assert len(data["receipts"]) == 3
    assert all(r["receipt"] == "GITHUB_MERGED_IN_LANE_LEDGER" for r in data["receipts"])
    assert data["product_acceptance"] == "UNVERIFIED_BY_THIS_CHECKPOINT"


@pytest.mark.parametrize("lost", ["all", "item-only", "state-only", "accepted-local"])
def test_interrupted_or_corrupt_canonical_history_cannot_replay_accepted_child(
    tmp_path: Path, lost: str
):
    lane = tmp_path / "lane"
    _plan(lane)
    if lost == "accepted-local":
        items = [_item(1)]
        state = {"items": {items[0]["id"]: {"outcome": "ACCEPTED_LOCAL", "packets": ["P1"]}}}
    else:
        items, state = _merged_state(1)
    checkpoints.record_checkpoints(lane, REPO, HEAD, items, state)
    if lost in ("all", "item-only"):
        items = []
    if lost in ("all", "state-only", "accepted-local"):
        state = {}
    # Historical advisory receipts may NEVER grant dispatch. But they SHOULD
    # prevent replay if canonical source-of-truth now contradicts them.
    assert _ready(lane, items, state) == []


def test_revised_plan_does_not_inherit_prior_checkpoint_replay_veto(tmp_path: Path):
    lane = tmp_path / "lane"
    plan = _plan(lane)
    items, state = _merged_state(1)
    checkpoints.record_checkpoints(lane, REPO, HEAD, items, state)
    plan["subtasks"][0]["done_when"] += " verified after roadmap revision"
    atomic_json(lane / "decompositions" / f"{PARENT}.json", plan)
    assert _ready(lane, [], {}) == [f"{PARENT}-t1"]


@pytest.mark.parametrize("bad", [
    {"receipts": 42},
    {"receipts": [{"receipt": {"not": "a string"}}]},
    {"receipts": [{"receipt": "GITHUB_MERGED_IN_LANE_LEDGER", "key": [],
                   "attempt_id": "bad-id"}]},
    {"receipts": [{"receipt": "GITHUB_MERGED_IN_LANE_LEDGER", "key": "t1",
                   "attempt_id": "some-other-plan-t1"}]},
])
def test_malformed_or_spoofed_checkpoint_receipts_fail_closed(tmp_path: Path, bad: dict):
    lane = tmp_path / "lane"
    plan = _plan(lane)
    items, state = _merged_state(1)
    checkpoints.record_checkpoints(lane, REPO, HEAD, items, state)
    path = checkpoints.checkpoint_path(lane, plan)
    record = json.loads(path.read_text(encoding="utf-8"))
    record.update(bad)
    atomic_json(path, record)
    # Malformed advisory data must never authorize or accidentally permit a
    # new local process by throwing before the canonical gate can run.
    assert _ready(lane, items, state) == []


def test_recovery_guard_does_not_block_independent_plan_in_same_repository(tmp_path: Path):
    lane = tmp_path / "lane"
    plan = _plan(lane)
    items, state = _merged_state(1)
    checkpoints.record_checkpoints(lane, REPO, HEAD, items, state)
    other = {**plan, "parent_id": "RM-knowledge-engine-web-independent-approved-work"}
    atomic_json(lane / "decompositions" / f"{other['parent_id']}.json", other)
    assert _ready(lane, [], {}) == [other["parent_id"] + "-t1"]


def test_locally_accepted_failed_review_can_use_existing_capped_revision_rules(tmp_path: Path):
    lane = tmp_path / "lane"
    _plan(lane)
    items = [_item(1)]
    state = {"items": {items[0]["id"]: {
        "outcome": "ACCEPTED_LOCAL", "packets": ["PRODUCT-FIRST"], "attempts": 1,
    }}}
    checkpoints.record_checkpoints(lane, REPO, HEAD, items, state)
    state["items"][items[0]["id"]]["outcome"] = "FAIL: SLICE_TESTS_FAILED"
    state["items"][items[0]["id"]]["attempts"] = 3
    assert _ready(lane, items, state) == [PARENT + "-t1-r1"]


def test_checkpoint_guard_is_read_only_even_when_blocking_replay(tmp_path: Path):
    lane = tmp_path / "lane"
    plan = _plan(lane)
    items, state = _merged_state(1)
    checkpoints.record_checkpoints(lane, REPO, HEAD, items, state)
    path = checkpoints.checkpoint_path(lane, plan)
    before = path.read_bytes()
    for _ in range(3):
        assert _ready(lane, [], {}) == []
    assert path.read_bytes() == before
