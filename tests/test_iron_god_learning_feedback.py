"""Prove advisory learning captures real strategy use, not invented success."""
from __future__ import annotations

import json
import sqlite3

import pytest

from project_orchestrator import iron_god_backlog as backlog
from project_orchestrator import iron_god_development_memory as memory
from project_orchestrator import iron_god_failure_strategy as strategy
from project_orchestrator import iron_god_learning_feedback as feedback
from project_orchestrator import iron_god_engineering_evidence as evidence
from project_orchestrator import supervisor_runtime

REPO = "jweter/VoiceStudio"
SHA = "a" * 40


def _item(ident: str) -> dict:
    return {
        "id": ident, "repository": REPO, "status": "approved",
        "assignee": backlog.AGENT_ID, "level": 1,
        "build": {"module_path": "backend/services/valid.py", "function_name": "valid"},
    }


def _packet(ident: str, packet_id: str, hint: dict | None = None) -> dict:
    packet = {
        "packet_id": packet_id, "repository": REPO, "base_sha": SHA,
        "slice": _item(ident),
    }
    if hint is not None:
        packet["recovery_strategy"] = hint
    return packet


def _record(root, ident: str, packet_id: str, *, passed: bool, hint: dict | None = None,
            outcome: str | None = None) -> None:
    state = "PASS" if passed else "FAIL"
    summary = "ok" if passed else "SLICE_TESTS_FAILED"
    memory.record_attempt(
        root, _packet(ident, packet_id, hint),
        {"status": state, "summary": summary,
         "plan": {"mode": "build", "module_path": "backend/services/valid.py"}},
        outcome if outcome is not None else ("ACCEPTED_LOCAL" if passed else "FAIL: SLICE_TESTS_FAILED"),
    )


def _hint(root, ident: str, source: str) -> dict:
    item = _item(ident)
    hint = strategy.recovery_strategy(root, item, {
        "packets": [source], "attempts": 1, "outcome": "FAIL: SLICE_TESTS_FAILED",
    })
    assert hint is not None
    assert hint["strategy"] == "SLICE_TESTS_FAILED"
    return hint


def test_actual_failure_guidance_outcome_is_replay_safe_and_visible_in_mcp(tmp_path):
    lane = tmp_path / "iron-god-products"
    _record(lane, "S1", "P1", passed=False)
    hint = _hint(lane, "S1", "P1")
    assert "fresh algorithm" in strategy.coding_guidance(_packet("S1", "P2", hint))
    _record(lane, "S1", "P2", passed=True, hint=hint)
    _record(lane, "S1", "P2", passed=True, hint=hint)  # same immutable result

    with sqlite3.connect(memory.db_path(lane)) as db:
        assert db.execute("SELECT packet_id,strategy,revision,evidence_packet FROM strategy_exposures").fetchall() == [
            ("P2", "SLICE_TESTS_FAILED", 1, "P1"),
        ]
        assert db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 2

    report = feedback.snapshot(lane, repository=REPO)
    assert report["independent_recovery_lineages"] == 1
    assert report["strategies"][0]["worker_reported_local_passes"] == 1
    assert report["strategies"][0]["status"] == "INSUFFICIENT_INDEPENDENT_LOCAL_EVIDENCE"
    assert report["learning_effectiveness_proven"] is False
    assert report["automatic_promotion_authorized"] is False
    projection = evidence.engineering_bundle(tmp_path, repository=REPO)
    assert projection["learning"]["effectiveness"] == report
    assert "P2" not in json.dumps(projection["learning"]["effectiveness"])


def test_a_replayed_packet_cannot_gain_new_recovery_credit(tmp_path):
    _record(tmp_path, "S1", "P1", passed=False)
    _record(tmp_path, "S1", "P2", passed=True)
    hint = _hint(tmp_path, "S1", "P1")
    with pytest.raises(sqlite3.IntegrityError, match="ATTEMPT_REPLAY_CONFLICT"):
        _record(tmp_path, "S1", "P2", passed=True, hint=hint)
    assert feedback.snapshot(tmp_path)["strategies_observed"] == 0


def test_forged_and_cross_task_evidence_cannot_create_strategy_exposure(tmp_path):
    _record(tmp_path, "S1", "P1", passed=False)
    valid = _hint(tmp_path, "S1", "P1")
    for ident, pid, hint in [
        ("S2", "P2", valid),  # wrong task
        ("S1", "P3", {**valid, "strategy": "STATIC_LINE_LENGTH"}),  # wrong error class
        ("S1", "P4", {**valid, "evidence_packet": "NOT_RECORDED"}),
        ("S1", "P5", {**valid, "revision": 999}),
    ]:
        _record(tmp_path, ident, pid, passed=True, hint=hint)
    assert feedback.snapshot(tmp_path)["strategies_observed"] == 0


def test_repeated_revisions_count_one_independent_lineage(tmp_path):
    ident = "RM-VoiceStudio-sing-build-s1"
    _record(tmp_path, ident, "P1", passed=False)
    for revision in range(1, 4):
        new_id = ident + f"-r{revision}"
        # Each revision has its own failure packet, but belongs to one task lineage.
        _record(tmp_path, new_id, f"F{revision}", passed=False)
        _record(tmp_path, new_id, f"R{revision}", passed=revision == 3,
                hint=_hint(tmp_path, new_id, f"F{revision}"))
    report = feedback.snapshot(tmp_path)
    assert report["strategies_observed"] == 3
    assert report["independent_recovery_lineages"] == 1
    assert report["strategies"][0]["independent_lineages"] == 1
    assert report["strategies"][0]["worker_reported_local_passes"] == 1


def test_three_independent_workers_are_only_candidates_not_proven_product_learning(tmp_path):
    for n in range(3):
        ident = f"FEATURE-{n}"
        source = f"F{n}"
        _record(tmp_path, ident, source, passed=False)
        _record(tmp_path, ident, f"R{n}", passed=n != 2, hint=_hint(tmp_path, ident, source))
    report = feedback.snapshot(tmp_path)
    row = report["strategies"][0]
    assert row["independent_lineages"] == 3
    assert row["worker_reported_local_passes"] == 2
    assert row["worker_reported_failures"] == 1
    assert row["status"] == "CANDIDATE_FOR_INDEPENDENT_REVIEW"
    assert report["learning_effectiveness_proven"] is False
    assert report["independent_github_merge_verified"] is False
    assert report["native_product_acceptance"] == "UNVERIFIED"


def test_pass_word_without_local_acceptance_does_not_count(tmp_path):
    _record(tmp_path, "S1", "P1", passed=False)
    _record(tmp_path, "S1", "P2", passed=True, hint=_hint(tmp_path, "S1", "P1"),
            outcome="FAIL: canonical preflight failed")
    assert feedback.snapshot(tmp_path)["strategies"][0]["worker_reported_local_passes"] == 0


def test_legacy_and_missing_databases_remain_read_only(tmp_path):
    lane = tmp_path / "iron-god-products"
    assert feedback.snapshot(lane)["status"] == "UNAVAILABLE"
    assert not memory.db_path(lane).exists()
    _record(lane, "S1", "P1", passed=False)
    db = memory.db_path(lane)
    with sqlite3.connect(db) as connection:
        connection.execute("DROP TABLE strategy_exposures")
    before = db.stat().st_size
    result = feedback.snapshot(lane)
    assert result["status"] == "LEGACY_LEDGER_NO_EXPOSURES"
    assert result["worker_attempts_observed"] == 1
    assert db.stat().st_size == before


def test_supervisor_separates_real_local_feedback_from_empty_jev_history(tmp_path):
    lane = tmp_path / "iron-god-products"
    _record(lane, "S1", "P1", passed=False)
    _record(lane, "S1", "P2", passed=True, hint=_hint(lane, "S1", "P1"))
    result = supervisor_runtime.refresh_decision_learning(tmp_path)
    assert result["attributed_outcomes"] == 0
    assert result["learning"]["policies"] == []
    assert result["development_feedback"]["independent_recovery_lineages"] == 1
    assert result["development_feedback"]["learning_effectiveness_proven"] is False
    assert result["development_feedback"]["automatic_promotion_authorized"] is False


def test_report_never_leaks_prompts_replies_or_worker_instructions(tmp_path):
    _record(tmp_path, "S1", "P1", passed=False)
    _record(tmp_path, "S1", "P2", passed=True, hint=_hint(tmp_path, "S1", "P1"))
    with sqlite3.connect(memory.db_path(tmp_path)) as db:
        db.execute("UPDATE attempts SET prompt='SECRET_PROMPT', reply='PRIVATE_MODEL_RESPONSE'")
    result = feedback.snapshot(tmp_path)
    assert "SECRET" not in json.dumps(result)
    assert "PRIVATE" not in json.dumps(result)
    assert result["new_worker_or_owner_grant"] is False
