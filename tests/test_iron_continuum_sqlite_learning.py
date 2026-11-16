"""IRON CONTINUUM: independent SQLite learning must not count retry IDs as slices."""
from __future__ import annotations

import json
import sqlite3

from project_orchestrator import iron_god_development_memory as memory


def _packet(packet_id: str, item_id: str, repo="jweter/VoiceStudio") -> dict:
    return {
        "packet_id": packet_id, "repository": repo, "base_sha": "a" * 40,
        "slice": {"id": item_id},
    }


def _handback(*, passed: bool = False) -> dict:
    return {
        "status": "PASS" if passed else "FAIL",
        "summary": "ok" if passed else "UNEXPECTED_UNIT: helper wrongly hoisted",
        "plan": {"mode": "directed", "module_path": "src/mod.py", "targets": ["helper"],
                 "direction": "change only the named helper"},
        "directed": {"model": "ollama/qwen2.5-coder:7b", "prompt": "P", "reply": "R"},
    }


def test_three_revisions_of_one_slice_are_not_three_independent_learning_examples(tmp_path):
    root = "RM-VoiceStudio-owner-approved-feature-t1"
    for n, item in enumerate([root, root + "-r1", root + "-r2"], 1):
        memory.record_attempt(tmp_path, _packet(f"P{n}", item), _handback(), "FAIL")
    assert memory.rules_for(tmp_path) == []
    assert memory.stats(tmp_path)["slices"] == 1
    assert memory.stats(tmp_path)["attempts"] == 3


def test_a_second_genuine_lineage_qualifies_and_cites_all_original_packet_ids(tmp_path):
    root = "RM-VoiceStudio-owner-approved-feature-t1"
    another = "RM-VoiceStudio-owner-approved-feature-t2"
    memory.record_attempt(tmp_path, _packet("P1", root), _handback(), "FAIL")
    memory.record_attempt(tmp_path, _packet("P2", root + "-r1"), _handback(), "FAIL")
    memory.record_attempt(tmp_path, _packet("P3", another), _handback(), "FAIL")

    (rule,) = memory.rules_for(tmp_path)
    assert rule["failure_class"] == "REPLY_UNEXPECTED_UNIT"
    assert rule["kind"] == "INFERRED_RECOMMENDATION"  # NOT a verified skill or owner instruction
    assert rule["slices"] == 2
    assert rule["evidence"] == {root: ["P1", "P2"], another: ["P3"]}
    assert rule["repositories"] == ["jweter/VoiceStudio"]
    assert memory.stats(tmp_path)["slices"] == 2


def test_previously_inflated_legacy_rule_is_retracted_without_erasing_attempts(tmp_path):
    root = "RM-VoiceStudio-owner-approved-feature-t1"
    for n, item in enumerate([root, root + "-r1"], 1):
        memory.record_attempt(tmp_path, _packet(f"P{n}", item), _handback(), "FAIL")

    db = memory.db_path(tmp_path)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "INSERT INTO rules VALUES (?,?,?,?,?,?)",
            ("REPLY_UNEXPECTED_UNIT", "legacy synthetic rule",
             json.dumps({root: ["P1"], root + "-r1": ["P2"]}), 2,
             "INFERRED_RECOMMENDATION", "2026-11-15T00:00:00Z"),
        )
    assert memory.rules_for(tmp_path) == []
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM rules").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 2


def test_first_attempt_metrics_are_per_lineage_not_per_retry(tmp_path):
    root = "RM-VoiceStudio-owner-approved-feature-t1"
    other = "RM-VoiceStudio-owner-approved-feature-t2"
    memory.record_attempt(tmp_path, _packet("P1", root), _handback(), "FAIL")
    memory.record_attempt(tmp_path, _packet("P2", root + "-r1"), _handback(passed=True),
                          "ACCEPTED_LOCAL")
    memory.record_attempt(tmp_path, _packet("P3", other), _handback(passed=True),
                          "ACCEPTED_LOCAL")
    stats = memory.stats(tmp_path)
    assert stats["attempts"] == 3
    assert stats["slices"] == 2
    assert stats["first_attempt_judged"] == 2
    assert stats["first_attempt_accepted"] == 1
    assert stats["first_attempt_rate"] == 0.5
