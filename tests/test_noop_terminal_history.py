"""Regression tests for the nonproductive bounded-slice retry budget."""
import json

from project_orchestrator.activation_capability import repeated_noop_slice_evidence


SLICE = {
    "repository": "jweter/VoiceStudio",
    "capability_id": "singing-s1-capability-schema",
    "slice_id": "s1-capability-schema-catalogue-filtering",
    "objective": "Add singing capability catalogue filtering.",
    "acceptance": ["Singing support is independently filterable."],
    "allowed_paths": ["backend/**", "tests/**"],
    "forbidden_paths": [".env"],
}


def _record(order, status, state="FAILED", authority=None):
    return {
        "issued_at": f"2026-10-18T20:{order:02d}:00+00:00",
        "state": state,
        "result_status": status,
        "execution_slice": SLICE if authority is None else authority,
    }


def _check(tmp_path, grants, authority=SLICE):
    (tmp_path / "hands-off-grant-lifecycle.json").write_text(
        json.dumps({"grants": grants}), encoding="utf-8"
    )
    return repeated_noop_slice_evidence(tmp_path, "jweter/VoiceStudio", authority)


def test_pending_attempt_does_not_erase_two_verified_nonproductive_outcomes(tmp_path):
    evidence = _check(tmp_path, {
        "first": _record(1, "NO_OP"),
        "second": _record(2, "NO_OP"),
        "third": _record(3, None, "CLAIMED"),
    })
    assert evidence["exhausted"] is True
    assert [row["grant_id"] for row in evidence["recent_attempts"]] == ["first", "second"]


def test_verified_success_resets_consecutive_noop_streak(tmp_path):
    evidence = _check(tmp_path, {
        "first": _record(1, "NO_OP"),
        "second": _record(2, "NO_OP"),
        "success": _record(3, "PR_OPEN", "COMPLETED"),
        "pending": _record(4, None, "CLAIMED"),
    })
    assert evidence["exhausted"] is False


def test_revised_scope_gets_its_own_retry_budget(tmp_path):
    revised = {**SLICE, "objective": "A different authorized capability implementation."}
    evidence = _check(tmp_path, {
        "first": _record(1, "NO_OP"),
        "second": _record(2, "NO_OP"),
    }, authority=revised)
    assert evidence["exhausted"] is False
    assert evidence["attempt_count"] == 0


def test_single_noop_does_not_exhaust_budget(tmp_path):
    evidence = _check(tmp_path, {
        "first": _record(1, "NO_OP"),
        "pending": _record(2, None, "CLAIMED"),
    })
    assert evidence["exhausted"] is False
