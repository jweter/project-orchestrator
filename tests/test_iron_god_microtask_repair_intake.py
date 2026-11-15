"""Repair proposals never become dispatchable work by copying backlog-shaped JSON."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from project_orchestrator import iron_god_backlog as backlog
from project_orchestrator import iron_god_microtask_progress as progress
from project_orchestrator import iron_god_microtask_repair_advisor as advisor
from project_orchestrator import iron_god_microtask_repair_intake as intake
from project_orchestrator import iron_god_task_decomposer as decomposer
from project_orchestrator.state_io import atomic_json

PARENT = "RM-web-cap-feature-00001"
REPO = "jweter/knowledge-engine-web"
HEAD = "d" * 40


@pytest.fixture
def ready(tmp_path: Path):
    plan = {
        "schema_version": 1,
        "parent_id": PARENT,
        "repository": REPO,
        "status": "BLOCKED",
        "planned_at_head": "c" * 40,
        "roadmap_ref": "docs/OWNER_APPROVED_ROADMAP_web.md",
        "slice_acceptance": [
            "Preserve title normalization semantics",
            "Report the title statistics",
        ],
        "allowed_paths": ["pkg/**", "tests/**"],
        "forbidden_paths": ["pkg/secret/**"],
        "coverage": {"1": ["t1"], "2": ["t2"]},
        "subtasks": [
            {
                "key": "t1",
                "kind": "edit",
                "module_path": "pkg/one.py",
                "test_path": "tests/test_one.py",
                "depends_on": [],
            },
            {"key": "t2", "module_path": "pkg/two.py", "depends_on": ["t1"]},
            {"key": "t3", "module_path": "pkg/sibling.py", "depends_on": []},
        ],
    }
    atomic_json(decomposer.plan_path(tmp_path, PARENT), plan)
    t1, r1, t3 = PARENT + "-t1", PARENT + "-t1-r1", PARENT + "-t3"
    items = [
        {
            "id": ident,
            "repository": REPO,
            "status": "approved",
            "interfaces": ["pkg/one.py"],
        }
        for ident in (t1, r1)
    ]
    items.append(
        {
            "id": t3,
            "repository": REPO,
            "status": "approved",
            "interfaces": ["pkg/sibling.py"],
        }
    )
    state = {
        "items": {
            t1: {"outcome": "FAIL: SLICE_TESTS_FAILED", "attempts": 3},
            r1: {
                "outcome": "FAIL: SLICE_TESTS_FAILED",
                "attempts": 3,
                "packet_id": "PRODUCT-FAILED",
            },
            t3: {"outcome": "MERGED", "attempts": 1, "packets": ["PRODUCT-MERGED"]},
        }
    }
    assert progress.record_checkpoints(tmp_path, REPO, HEAD, items, state) == [PARENT]
    model_answer = {
        "steps": [
            {
                "key": "s1",
                "action": "change",
                "target": "pkg/one.py",
                "objective": "Fix the original title normalization without changing interfaces",
                "verification": "Run the original title acceptance test against the changed function",
                "criteria": [1],
                "depends_on": [],
            },
        ]
    }
    result = advisor.propose_current(
        tmp_path,
        PARENT,
        REPO,
        HEAD,
        "t1",
        authority=lambda: True,
        ask_local=lambda _prompt: model_answer,
        model_provenance={
            "provider": "LOCAL_OLLAMA_ONLY",
            "model_used": "qwen2.5-coder:7b",
        },
    )
    assert result["status"] == "ADVISORY_RECORDED"
    return tmp_path, Path(result["path"]).stem, items, state


def stage(
    ready, *, authority=lambda: True, advice=None, items=None, state=None, head=HEAD
):
    lane, digest, valid_items, ledger = ready
    return intake.stage_for_review(
        lane,
        advice or digest,
        REPO,
        head,
        items=valid_items if items is None else items,
        state=ledger if state is None else state,
        authority=authority,
    )


def test_review_item_is_valid_proposed_backlog_shape_but_unapprovable_without_red_test(
    ready,
) -> None:
    staged = stage(ready)
    assert staged["status"] == "REVIEW_PACKAGE_STAGED"
    package = staged["package"]
    item = package["review_item"]
    assert package["advisory_only"] and package["review_required"]
    assert package["ready_for_backlog_import"] is False
    assert package["ready_for_automatic_roadmap_promotion"] is False
    assert package["dispatchable"] is False
    assert package["attempt_budget_reset"] is False
    assert item["status"] == "proposed"
    assert item["level"] == 2
    assert item["interfaces"] == ["pkg/one.py"]
    assert item["tests"] == ["tests/test_one.py"]
    assert item["acceptance_criteria"] == ["Preserve title normalization semantics"]
    assert item["repair_origin"]["already_completed_keys"] == ["t3"]
    assert item["repair_origin"]["blocked_keys"] == ["t1", "t2"]
    assert item["repair_origin"]["lineage_attempts_remain_spent"] is True
    assert item["repair_origin"]["failed_attempt_id"] == PARENT + "-t1-r1"
    assert backlog.item_problems(item) == []
    assert backlog.next_item([item], ready[3], REPO, max_level=2) is None
    # Even changing status and assignee by hand is NOT enough for dispatch:
    # missing a strict directed edit spec, real failing test_source and approved_base.
    assert "APPROVED_ITEM_WITHOUT_DIRECTION" in backlog.item_problems(
        {
            **item,
            "status": "approved",
            "assignee": "iron-god",
        }
    )
    promoted = backlog.promote_by_roadmap(
        [item], {REPO: {"docs/OWNER_APPROVED_ROADMAP_web.md"}}
    )
    assert promoted[0]["status"] == "proposed"
    assert len(ready[2]) == 3  # never inserted into canonical backlog
    assert staged["path"].endswith(ready[1] + ".json")


def test_identical_advice_and_evidence_is_idempotent_across_restarts(ready) -> None:
    first = stage(ready)
    assert first["status"] == "REVIEW_PACKAGE_STAGED"
    raw = Path(first["path"]).read_bytes()
    again = stage(ready)
    assert again["status"] == "ALREADY_STAGED"
    assert Path(first["path"]).read_bytes() == raw


def test_owner_deny_blocks_staging_without_mutating_lane(ready) -> None:
    result = stage(ready, authority=lambda: False)
    assert result == {"status": "OWNER_AUTHORITY_DENIED"}
    assert not (ready[0] / intake.INBOX_DIR).exists()


def test_stale_checkpoint_and_mismatched_ledger_are_rejected(ready) -> None:
    lane, digest, items, state = ready
    stale = json.loads(json.dumps(state))
    stale["items"][PARENT + "-t1-r1"]["attempts"] = 0
    assert (
        stage(ready, state=stale)["status"]
        == "CANONICAL_FAILED_ATTEMPT_MISSING_OR_CHANGED"
    )
    packet_drift = json.loads(json.dumps(state))
    packet_drift["items"][PARENT + "-t1-r1"]["packet_id"] = "PRODUCT-ANOTHER"
    assert (
        stage(ready, state=packet_drift)["status"]
        == "CANONICAL_PACKET_EVIDENCE_MISSING_OR_CHANGED"
    )
    assert (
        stage(ready, head="e" * 40)["status"] == "ADVICE_AUTHORITY_OR_IDENTITY_MISMATCH"
    )

    plan = json.loads(decomposer.plan_path(lane, PARENT).read_text(encoding="utf-8"))
    checkpoint_path = progress.checkpoint_path(lane, plan)
    cp = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    cp["checkpointed_at_utc"] = "new snapshot invalidates original advice"
    atomic_json(checkpoint_path, cp)
    assert stage(ready)["status"] == "ADVICE_EVIDENCE_MISMATCH"


def test_local_advice_tampering_or_failed_outline_is_rejected(ready) -> None:
    lane, digest, _, _ = ready
    path = lane / advisor.ADVICE_DIR / f"{digest}.json"
    source = json.loads(path.read_text(encoding="utf-8"))
    source["outcome"]["proposal"]["steps"][0]["target"] = "pkg/secret/escape.py"
    atomic_json(path, source)
    assert stage(ready)["status"] == "REPAIR_OUTLINE_NOT_CANONICAL"


@pytest.mark.parametrize("reserved_path", ["pkg/one.py", "tests/test_one.py"])
def test_conflicting_live_approved_work_and_duplicate_id_block_staging(
    ready, reserved_path: str
) -> None:
    lane, digest, items, state = ready
    blocking = {
        "id": "OTHER-FEATURE",
        "status": "approved",
        "repository": REPO,
        "interfaces": [reserved_path],
    }
    assert (
        stage(ready, items=[*items, blocking])["status"]
        == "EXISTING_APPROVED_WORK_OWNS_TARGET"
    )
    collision = {
        "id": "IGR-" + digest[:20],
        "status": "proposed",
        "repository": REPO,
        "interfaces": [],
    }
    assert (
        stage(ready, items=[*items, collision])["status"]
        == "BACKLOG_ID_COLLISION_OR_MALFORMED_ITEM"
    )


def test_invalid_filename_is_rejected_without_reading_arbitrary_paths(ready) -> None:
    assert stage(ready, advice="../../exfiltrate") == {
        "status": "INVALID_ADVICE_IDENTIFIER"
    }


def test_corrupt_existing_review_package_is_not_silently_replaced(ready) -> None:
    first = stage(ready)
    path = Path(first["path"])
    path.write_text('{"tampered":true}', encoding="utf-8")
    assert stage(ready)["status"] == "INTAKE_RECORD_UNVERIFIED"
    assert path.read_text(encoding="utf-8") == '{"tampered":true}'


def test_intake_cli_fail_closed_on_owner_grant_and_git_head(
    ready,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import iron_god_repair_intake as cli
    from project_orchestrator import execution_authority as execution

    lane, digest, _, _ = ready
    args = [
        "--lane",
        str(lane),
        "--release-repo",
        str(lane),
        "--clone",
        str(lane),
        "--advice-sha",
        digest,
        "--repository",
        REPO,
        "--head",
        HEAD,
    ]
    monkeypatch.setenv("IRON_GOD_ACCEPTED_SHA", "a" * 40)
    monkeypatch.setattr(
        execution,
        "evaluate_execution_authority",
        lambda **_kw: SimpleNamespace(permitted=False),
    )
    assert cli.main(args) == 2
    assert (
        json.loads(capsys.readouterr().out)["status"]
        == "OWNER_AUTHORITY_OR_EXACT_HEAD_DENIED"
    )

    monkeypatch.setattr(
        execution,
        "evaluate_execution_authority",
        lambda **_kw: SimpleNamespace(permitted=True),
    )
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *_a, **_kw: SimpleNamespace(stdout="e" * 40),
    )
    assert cli.main(args) == 2
    assert (
        json.loads(capsys.readouterr().out)["status"]
        == "OWNER_AUTHORITY_OR_EXACT_HEAD_DENIED"
    )
    assert not (lane / intake.INBOX_DIR).exists()


def test_intake_cli_refuses_unverified_backlog_before_writing(
    ready,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import iron_god_repair_intake as cli
    from project_orchestrator import execution_authority as execution

    lane, digest, _, _ = ready
    args = [
        "--lane",
        str(lane),
        "--release-repo",
        str(lane),
        "--clone",
        str(lane),
        "--advice-sha",
        digest,
        "--repository",
        REPO,
        "--head",
        HEAD,
    ]
    monkeypatch.setattr(
        execution,
        "evaluate_execution_authority",
        lambda **_kw: SimpleNamespace(permitted=True),
    )
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *_a, **_kw: SimpleNamespace(stdout=HEAD),
    )
    # No engineering/state/iron-god-backlog.json here: missing source
    # must NOT be mistaken for empty (hence conflict-free) state.
    assert cli.main(args) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "BACKLOG_INVALID"
    assert not (lane / intake.INBOX_DIR).exists()


def test_cli_sees_runtime_admitted_file_reservations(
    ready,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import iron_god_repair_intake as cli
    from project_orchestrator import execution_authority as execution
    from project_orchestrator import iron_god_roadmap_refill as refill

    lane, digest, _items, state = ready
    (lane / "engineering" / "state").mkdir(parents=True)
    atomic_json(lane / backlog.BACKLOG_PATH, {"items": []})
    atomic_json(backlog.state_path(lane), state)
    monkeypatch.setenv("IRON_GOD_ACCEPTED_SHA", "a" * 40)
    monkeypatch.setattr(
        execution,
        "evaluate_execution_authority",
        lambda **_kw: SimpleNamespace(permitted=True),
    )
    monkeypatch.setattr(
        cli.subprocess, "run", lambda *_a, **_kw: SimpleNamespace(stdout=HEAD)
    )
    seen: list[int] = []

    def actual_runtime_inventory(_lane, static):
        seen.append(len(static))
        return [
            {
                "id": "RUNTIME-EXISTING",
                "status": "approved",
                "repository": REPO,
                "interfaces": ["tests/test_one.py"],
            }
        ]

    monkeypatch.setattr(refill, "admitted_items", actual_runtime_inventory)
    args = [
        "--lane",
        str(lane),
        "--release-repo",
        str(lane),
        "--clone",
        str(lane),
        "--advice-sha",
        digest,
        "--repository",
        REPO,
        "--head",
        HEAD,
    ]
    assert cli.main(args) == 2
    assert (
        json.loads(capsys.readouterr().out)["status"]
        == "EXISTING_APPROVED_WORK_OWNS_TARGET"
    )
    assert seen == [0]
    assert not (lane / intake.INBOX_DIR).exists()


def test_cli_rejects_corrupted_runtime_inventory_even_if_static_backlog_valid(
    ready,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import iron_god_repair_intake as cli
    from project_orchestrator import execution_authority as execution
    from project_orchestrator import iron_god_roadmap_refill as refill

    lane, digest, _items, state = ready
    (lane / "engineering" / "state").mkdir(parents=True)
    atomic_json(lane / backlog.BACKLOG_PATH, {"items": []})
    atomic_json(backlog.state_path(lane), state)
    refill.store_path(lane).write_text("{corrupt-json", encoding="utf-8")
    monkeypatch.setenv("IRON_GOD_ACCEPTED_SHA", "a" * 40)
    monkeypatch.setattr(
        execution,
        "evaluate_execution_authority",
        lambda **_kw: SimpleNamespace(permitted=True),
    )
    monkeypatch.setattr(
        cli.subprocess, "run", lambda *_a, **_kw: SimpleNamespace(stdout=HEAD)
    )
    args = [
        "--lane",
        str(lane),
        "--release-repo",
        str(lane),
        "--clone",
        str(lane),
        "--advice-sha",
        digest,
        "--repository",
        REPO,
        "--head",
        HEAD,
    ]
    assert cli.main(args) == 2
    assert (
        json.loads(capsys.readouterr().out)["status"] == "RUNTIME_ADMISSIONS_UNVERIFIED"
    )
    assert not (lane / intake.INBOX_DIR).exists()
