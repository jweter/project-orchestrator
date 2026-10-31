"""Development memory: observed facts per attempt, rules only from repeated, independent failures."""

from __future__ import annotations

import json

import pytest

from project_orchestrator import iron_god_development_memory as dm


def packet(packet_id, item_id, repo="jweter/knowledge-engine-ai"):
    return {"packet_id": packet_id, "repository": repo, "base_sha": "b" * 40,
            "slice": {"id": item_id} if item_id else None}


def handback(status, summary, **directed):
    return {"status": status, "summary": summary,
            "plan": {"mode": "directed", "module_path": "pkg/mod.py", "targets": ["f"], "direction": "do it"},
            "directed": {"model": "ollama/qwen2.5-coder:7b", "prompt": "P", "reply": "R", **directed}}


# The pilot's real worker summaries, 2026-10-28/29.
@pytest.mark.parametrize(("status", "summary", "directed", "expected"), [
    ("FAIL", "SLICE_TESTS_FAILED", {"test_failure": "x.py:18: error: Function is missing a return type annotation  "
                                                   "[no-untyped-def]"}, "STATIC_MYPY_UNTYPED"),
    ("FAIL", "DIRECTED_REPLY_REJECTED: UNEXPECTED_UNIT: TextExtractor", {}, "REPLY_UNEXPECTED_UNIT"),
    ("FAIL", "DIRECTED_REPLY_REJECTED: MALFORMED_BLOCK: block 1: illegal target for annotation line 1", {},
     "REPLY_MALFORMED_BLOCK"),
    ("FAIL", "BACKLOG_SLICE_INVALID: DIRECT_PATH_FORBIDDEN_OR_UNDER_REVIEW: a.py", {}, "BLOCKED_CONFLICT"),
    ("FAIL", "COMPLETED_EXTERNALLY: the approved tests pass on abc", {}, "COMPLETED_EXTERNALLY"),
    ("FAIL", "SLICE_TESTS_FAILED", {"test_failure": "E   assert 1 == 2"}, "SLICE_TESTS_FAILED"),
    ("FAIL", "SLICE_TESTS_FAILED", {"test_failure": "a.py:3:101: E501 Line too long"}, "STATIC_LINE_LENGTH"),
    ("PASS", "ok", {}, "PASSED"),
    ("FAIL", "canonical preflight failed", {}, "PREFLIGHT_FAILED"),
    ("ENVIRONMENT_FAILURE", "DIRECTED_WORKER_ERROR: OSError", {}, "ENVIRONMENT"),
])
def test_failures_are_classified_from_the_workers_own_result(status, summary, directed, expected):
    assert dm.classify(status, summary, directed) == expected


def test_an_attempt_keeps_its_observed_facts(tmp_path):
    dm.record_attempt(tmp_path, packet("P1", "KEA-7"), handback("FAIL", "UNEXPECTED_UNIT: X"), "FAIL: x")
    dm.record_attempt(tmp_path, packet("P1", "KEA-7"), handback("FAIL", "UNEXPECTED_UNIT: X"), "FAIL: x")  # idempotent
    import sqlite3
    with sqlite3.connect(dm.db_path(tmp_path)) as connection:
        rows = connection.execute("SELECT item_id, base_sha, model, prompt, reply, failure_class, targets "
                                  "FROM attempts").fetchall()
    assert rows == [("KEA-7", "b" * 40, "ollama/qwen2.5-coder:7b", "P", "R", "REPLY_UNEXPECTED_UNIT", '["f"]')]


def test_a_rule_needs_the_same_failure_on_two_different_slices_and_cites_them(tmp_path):
    untyped = {"test_failure": "a.py:1: error: Function is missing a type annotation  [no-untyped-def]"}
    dm.record_attempt(tmp_path, packet("P1", "KEA-6"), handback("FAIL", "SLICE_TESTS_FAILED", **untyped), "FAIL")
    dm.record_attempt(tmp_path, packet("P2", "KEA-6"), handback("FAIL", "SLICE_TESTS_FAILED", **untyped), "FAIL")
    assert dm.rules_for(tmp_path) == []  # one slice failing twice is not independent evidence
    dm.record_attempt(tmp_path, packet("P3", "KEC-9", "jweter/knowledge-engine-core"),
                      handback("FAIL", "SLICE_TESTS_FAILED", **untyped), "FAIL")
    [rule] = dm.rules_for(tmp_path)
    assert rule["failure_class"] == "STATIC_MYPY_UNTYPED" and rule["kind"] == "INFERRED_RECOMMENDATION"
    assert rule["evidence"] == {"KEA-6": ["P1", "P2"], "KEC-9": ["P3"]} and "strict-mypy" in rule["rule"]
    assert dm.rules_for(tmp_path, "jweter/knowledge-engine-core") == [rule]
    assert dm.rules_for(tmp_path, "jweter/VoiceStudio") == []


def test_first_attempt_acceptance_counts_each_slice_once_and_ignores_what_the_model_never_judged(tmp_path):
    dm.record_attempt(tmp_path, packet("P1", "A"), handback("FAIL", "UNEXPECTED_UNIT: X"), "FAIL")
    dm.record_attempt(tmp_path, packet("P2", "A"), handback("PASS", "ok"), "ACCEPTED_LOCAL")  # second try
    dm.record_attempt(tmp_path, packet("P3", "B"), handback("PASS", "ok"), "ACCEPTED_LOCAL")
    dm.record_attempt(tmp_path, packet("P4", "C"), handback("FAIL", "COMPLETED_EXTERNALLY: x"), "COMPLETED")
    dm.record_attempt(tmp_path, packet("P5", None), handback("PASS", "ok"), "ACCEPTED_LOCAL")  # self-planned
    result = dm.stats(tmp_path)
    assert result["slices"] == 3 and result["first_attempt_judged"] == 2 and result["first_attempt_accepted"] == 1
    assert result["first_attempt_rate"] == 0.5


def test_backfill_reads_the_lane_records_already_on_disk(tmp_path, capsys):
    for sub in ("packets", "handbacks", "reports"):
        (tmp_path / sub).mkdir()
    (tmp_path / "packets" / "P1.json").write_text(json.dumps(packet("P1", "KEC-7")), encoding="utf-8")
    (tmp_path / "handbacks" / "P1.json").write_text(json.dumps(
        {"status": "FAIL", "summary": "DIRECTED_REPLY_REJECTED: MALFORMED_BLOCK: block 1"}), encoding="utf-8")
    (tmp_path / "reports" / "P1-handback-diagnostics.json").write_text(json.dumps(
        {"directed": {"reply": "R"}, "plan": {"mode": "directed", "targets": ["T"]}}), encoding="utf-8")
    (tmp_path / "backlog-state.json").write_text(json.dumps(
        {"items": {"KEC-7": {"packets": ["P1"], "outcome": "FAIL: DIRECTED_REPLY_REJECTED"}}}), encoding="utf-8")
    assert dm.main(["backfill", "--lane-root", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == {"recorded": 1}
    assert dm.stats(tmp_path)["failure_classes"] == {"REPLY_MALFORMED_BLOCK": 1}
