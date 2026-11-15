"""Local microtask progress receipts: durable but never an execution grant."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from project_orchestrator import iron_god_microtask_progress as progress
from project_orchestrator import iron_god_roadmap_refill as refill
from project_orchestrator.state_io import atomic_json

REPO = "jweter/knowledge-engine-web"
PARENT = "RM-knowledge-engine-web-cap-feature-01234567"


def plan(
    lane: Path, *, parent: str = PARENT, repository: str = REPO, second: bool = True
) -> dict:
    subtasks = [{"key": "t1", "depends_on": [], "module_path": "pkg/one.py"}]
    if second:
        subtasks.append(
            {"key": "t2", "depends_on": ["t1"], "module_path": "pkg/two.py"}
        )
    data = {
        "schema_version": 1,
        "parent_id": parent,
        "repository": repository,
        "roadmap_ref": "engineering/capability-graph.json#cap/feature",
        "planned_at_head": "a" * 40,
        "status": "ACTIVE",
        "coverage": {"1": ["t1"]},
        "subtasks": subtasks,
    }
    atomic_json(lane / "decompositions" / f"{parent}.json", data)
    return data


def item(ident: str) -> dict:
    return {"id": ident, "repository": REPO, "status": "approved", "level": 1}


def test_merged_child_is_durable_and_replay_is_idempotent(tmp_path: Path) -> None:
    active = plan(tmp_path)
    t1 = PARENT + "-t1"
    path = progress.checkpoint_path(tmp_path, active)
    assert progress.record_checkpoints(tmp_path, REPO, "b" * 40, [], {}) == [PARENT]
    baseline = json.loads(path.read_text(encoding="utf-8"))
    assert baseline["plan_status"] == "ACTIVE"
    assert baseline["subtasks"][0]["status"] == "NEW"
    assert baseline["receipts"] == []
    assert baseline["product_acceptance"] == "UNVERIFIED_BY_THIS_CHECKPOINT"

    items, state = (
        [item(t1)],
        {"items": {t1: {"outcome": "MERGED", "packets": ["PRODUCT-123"]}}},
    )
    assert progress.record_checkpoints(tmp_path, REPO, "c" * 40, items, state) == [
        PARENT
    ]
    data = path.read_bytes()
    snapshot = json.loads(data)
    assert snapshot["plan_status"] == "ACTIVE"  # one child still unfinished
    assert snapshot["subtasks"][0]["status"] == "DONE"
    assert snapshot["subtasks"][0]["receipt"] == "GITHUB_MERGED_IN_LANE_LEDGER"
    assert snapshot["subtasks"][1]["status"] == "NEW"
    assert snapshot["receipts"][0]["packet_ids"] == ["PRODUCT-123"]

    assert progress.record_checkpoints(tmp_path, REPO, "c" * 40, items, state) == []
    assert (
        path.read_bytes() == data
    )  # no churn, timestamps stable over repeated supervisor passes

    # A broken current state cannot erase the historical receipt; neither may
    # an old receipt masquerade as a currently verified completion.
    progress.record_checkpoints(tmp_path, REPO, "c" * 40, [], {})
    later = json.loads(path.read_text(encoding="utf-8"))
    assert later["subtasks"][0]["receipt"] == "NO_VERIFIED_COMPLETION_RECEIPT"
    assert later["receipts"] == snapshot["receipts"]


def test_failed_child_preserves_real_attempt_evidence_not_the_uncreated_retry(
    tmp_path: Path,
) -> None:
    active = plan(tmp_path, second=False)
    t1 = PARENT + "-t1"
    items = [item(t1)]
    state = {
        "items": {
            t1: {
                "outcome": "FAIL: SLICE_TESTS_FAILED",
                "attempts": 3,
                "packet_id": "PRODUCT-123",
            }
        }
    }
    progress.record_checkpoints(tmp_path, REPO, "b" * 40, items, state)
    path = progress.checkpoint_path(tmp_path, active)
    first = json.loads(path.read_text(encoding="utf-8"))["subtasks"][0]
    assert first["status"] == "FAILED"
    assert first["attempt_id"] == t1
    assert first["next_attempt_id"] == t1 + "-r1"
    assert first["outcome"] == "FAIL: SLICE_TESTS_FAILED"
    assert first["attempts"] == 3
    assert first["packet_ids"] == ["PRODUCT-123"]
    assert first["receipt"] == "NO_VERIFIED_COMPLETION_RECEIPT"

    # The next revision is now real and failed too: the observed ID follows
    # the last existing attempt, not the *next* still-undefined revision.
    r1 = t1 + "-r1"
    items.append(item(r1))
    state["items"][r1] = {
        "outcome": "FAIL: SLICE_TESTS_FAILED",
        "attempts": 3,
        "packet_id": "PRODUCT-456",
    }
    progress.record_checkpoints(tmp_path, REPO, "b" * 40, items, state)
    second = json.loads(path.read_text(encoding="utf-8"))["subtasks"][0]
    assert second["status"] == "EXHAUSTED"  # the canonical lineage cap, never bypassed
    assert second["attempt_id"] == r1
    assert second["next_attempt_id"] is None
    assert second["packet_ids"] == ["PRODUCT-456"]


def test_exhausted_child_generates_a_bounded_unprivileged_repair_brief(
    tmp_path: Path,
) -> None:
    active = plan(tmp_path)
    active["subtasks"].append(
        {
            "key": "t3",
            "depends_on": [],
            "module_path": "pkg/three.py",
        }
    )
    active["slice_acceptance"] = ["first behavior", "second behavior", "third behavior"]
    active["coverage"] = {"1": ["t1"], "2": ["t2"], "3": ["t3"]}
    atomic_json(tmp_path / "decompositions" / f"{PARENT}.json", active)

    t1, r1, t3 = PARENT + "-t1", PARENT + "-t1-r1", PARENT + "-t3"
    items = [item(t1), item(r1), item(t3)]
    state = {
        "items": {
            t1: {"outcome": "FAIL: SLICE_TESTS_FAILED", "attempts": 3},
            r1: {
                "outcome": "FAIL: SLICE_TESTS_FAILED",
                "attempts": 3,
                "packet_id": "PRODUCT-LAST",
            },
            t3: {"outcome": "MERGED", "packets": ["PRODUCT-VERIFIED"]},
        }
    }
    original = json.loads(json.dumps(state))
    assert progress.record_checkpoints(tmp_path, REPO, "c" * 40, items, state) == [
        PARENT
    ]
    path = progress.checkpoint_path(tmp_path, active)
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    (brief,) = checkpoint["repair_candidates"]
    assert brief["kind"] == "EXHAUSTED_LOCAL_MICROTASK"
    assert brief["failed_key"] == "t1"
    assert brief["observed_attempt_id"] == r1
    assert brief["blocked_keys"] == ["t1", "t2"]
    assert brief["already_completed_keys"] == ["t3"]
    assert [c["text"] for c in brief["affected_owner_criteria"]] == [
        "first behavior",
        "second behavior",
    ]
    assert brief["narrow_existing_paths"] == ["pkg/one.py"]
    assert brief["packet_ids"] == ["PRODUCT-LAST"]
    assert brief["requires_new_authorization"] is True
    assert brief["dispatchable"] is False
    assert brief["attempt_budget_reset"] is False
    assert checkpoint["subtasks"][2]["receipt"] == "GITHUB_MERGED_IN_LANE_LEDGER"
    assert state == original  # no attempt/owner-control or backlog mutation
    saved = path.read_bytes()
    assert progress.record_checkpoints(tmp_path, REPO, "c" * 40, items, state) == []
    assert path.read_bytes() == saved


def test_partial_plan_retains_residual_without_claiming_product_completion(
    tmp_path: Path,
) -> None:
    active = plan(tmp_path, second=False)
    active["status"] = "PARTIAL"
    active["residual"] = ["connect helper to the existing handler"]
    atomic_json(tmp_path / "decompositions" / f"{PARENT}.json", active)
    t1 = PARENT + "-t1"
    progress.record_checkpoints(
        tmp_path,
        REPO,
        "b" * 40,
        [item(t1)],
        {"items": {t1: {"outcome": "MERGED"}}},
    )
    data = json.loads(
        progress.checkpoint_path(tmp_path, active).read_text(encoding="utf-8")
    )
    (brief,) = data["repair_candidates"]
    assert brief["kind"] == "UNDELIVERED_RESIDUAL"
    assert brief["remaining_requirements"] == ["connect helper to the existing handler"]
    assert brief["already_completed_keys"] == ["t1"]
    assert brief["requires_new_authorization"] is True
    assert brief["dispatchable"] is False
    assert data["product_acceptance"] == "UNVERIFIED_BY_THIS_CHECKPOINT"


def test_healthy_plan_generates_no_repair_proposals(tmp_path: Path) -> None:
    active = plan(tmp_path)
    progress.record_checkpoints(tmp_path, REPO, "b" * 40, [], {})
    data = json.loads(
        progress.checkpoint_path(tmp_path, active).read_text(encoding="utf-8")
    )
    assert data["repair_candidates"] == []


def test_local_acceptance_is_not_merged_or_product_accepted(tmp_path: Path) -> None:
    active = plan(tmp_path, second=False)
    t1 = PARENT + "-t1"
    state = {"items": {t1: {"outcome": "ACCEPTED_LOCAL", "packet_id": "PRODUCT-456"}}}
    progress.record_checkpoints(tmp_path, REPO, "b" * 40, [item(t1)], state)
    found = json.loads(
        progress.checkpoint_path(tmp_path, active).read_text(encoding="utf-8")
    )
    assert found["subtasks"][0]["status"] == "PENDING"
    assert found["subtasks"][0]["receipt"] == "LOCAL_ACCEPTED_ONLY"
    assert found["receipts"][0]["receipt"] == "LOCAL_ACCEPTED_ONLY"
    assert found["product_acceptance"] == "UNVERIFIED_BY_THIS_CHECKPOINT"
    assert found["advisory_only"] is True


def test_new_plan_revision_does_not_inherit_old_completion_evidence(
    tmp_path: Path,
) -> None:
    old = plan(tmp_path)
    t1 = PARENT + "-t1"
    items, state = [item(t1)], {"items": {t1: {"outcome": "MERGED"}}}
    progress.record_checkpoints(tmp_path, REPO, "b" * 40, items, state)
    old_path = progress.checkpoint_path(tmp_path, old)
    assert old_path.exists()

    revised = plan(tmp_path, second=False)
    revised["planned_at_head"] = "d" * 40
    atomic_json(tmp_path / "decompositions" / f"{PARENT}.json", revised)
    new_path = progress.checkpoint_path(tmp_path, revised)
    assert old_path != new_path
    progress.record_checkpoints(tmp_path, REPO, "e" * 40, [], {})
    assert json.loads(old_path.read_text(encoding="utf-8"))["receipts"]
    assert json.loads(new_path.read_text(encoding="utf-8"))["receipts"] == []


def test_checkpoints_are_scoped_to_the_selected_repository(tmp_path: Path) -> None:
    ours = plan(tmp_path)
    other = plan(tmp_path, parent="RM-other-feature", repository="jweter/VoiceStudio")
    assert progress.record_checkpoints(tmp_path, REPO, "b" * 40, [], {}) == [PARENT]
    assert progress.checkpoint_path(tmp_path, ours).is_file()
    assert not progress.checkpoint_path(tmp_path, other).exists()
    assert progress.record_checkpoints(
        tmp_path, "jweter/VoiceStudio", "a" * 40, [], {}
    ) == ["RM-other-feature"]


def test_corrupt_checkpoint_is_replaced_without_changing_plan_or_execution(
    tmp_path: Path,
) -> None:
    active = plan(tmp_path, second=False)
    path = progress.checkpoint_path(tmp_path, active)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken json", encoding="utf-8")
    assert progress.record_checkpoints(tmp_path, REPO, "b" * 40, [], {}) == [PARENT]
    assert json.loads(path.read_text(encoding="utf-8"))["advisory_only"] is True
    assert (
        json.loads(
            (tmp_path / "decompositions" / f"{PARENT}.json").read_text(encoding="utf-8")
        )
        == active
    )
    assert progress.record_checkpoints(tmp_path, REPO, "b" * 40, [], {}) == []


def _seeded_clone(tmp_path: Path) -> tuple[Path, str]:
    clone = tmp_path / "clone"
    clone.mkdir()
    (clone / "README.md").write_text("# Minimal repository\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main", str(clone)], check=True)
    for args in (
        ("config", "user.email", "owner@example.test"),
        ("config", "user.name", "Owner"),
        ("add", "."),
        ("commit", "-q", "-m", "seed"),
    ):
        subprocess.run(
            ["git", "-C", str(clone), *args], check=True, capture_output=True
        )
    head = subprocess.run(
        ["git", "-C", str(clone), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return clone, head


def test_refill_emits_checkpoint_even_when_graph_source_is_unavailable(
    tmp_path: Path,
) -> None:
    lane = tmp_path / "lane"
    active = plan(lane)
    state_root = tmp_path / "state"
    state_root.mkdir()
    clone, head = _seeded_clone(tmp_path)

    report = refill.refill_repository(
        lane,
        state_root,
        REPO,
        clone,
        head,
        items=[],
        state={},
        reserved=set(),
        max_level=2,
        changed_since=lambda *_a: set(),
    )
    assert report["microtask_checkpoints"] == [PARENT]
    assert report["direction_required"]  # no owner graph; no work is authorized
    assert progress.checkpoint_path(lane, active).is_file()


def test_checkpoint_write_error_is_reported_without_blocking_refill(
    tmp_path: Path, monkeypatch
) -> None:
    lane = tmp_path / "lane"
    plan(lane)
    state_root = tmp_path / "state"
    state_root.mkdir()
    clone, head = _seeded_clone(tmp_path)

    def broken(*_args):
        raise PermissionError("checkpoint directory locked")

    monkeypatch.setattr(progress, "record_checkpoints", broken)
    report = refill.refill_repository(
        lane,
        state_root,
        REPO,
        clone,
        head,
        items=[],
        state={},
        reserved=set(),
        max_level=2,
        changed_since=lambda *_a: set(),
    )
    assert "PermissionError" in report["microtask_checkpoint_error"]
    assert report["direction_required"]  # unchanged roadmap authorization behavior
