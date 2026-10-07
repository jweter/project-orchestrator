from project_orchestrator.issue_closure import (
    assert_no_orphaned_actionable_issues,
    closure_decision,
    issue_debt_telemetry,
    normalize_issue,
    rank_actionable_issues,
)

def test_issue_ranking_prefers_product_impact():
    low = normalize_issue({"number": 1, "title": "cosmetic", "severity": 1}, product="rocksmith", repository="r")
    high = normalize_issue({"number": 2, "title": "cannot generate", "usability_impact": 4}, product="rocksmith", repository="r")
    assert [x["issue_number"] for x in rank_actionable_issues([low, high])] == [2, 1]

def test_owned_or_blocked_issue_does_not_enter_execution_frontier():
    owned = normalize_issue({"number": 1, "title": "owned", "owned": True}, product="p", repository="r")
    blocked = normalize_issue({"number": 2, "title": "blocked", "blocked": True}, product="p", repository="r")
    assert rank_actionable_issues([owned, blocked]) == []

def test_merge_is_not_enough_to_close_issue():
    item = normalize_issue({"number": 3, "title": "bug", "engineering_state": "CONFIRMED"}, product="p", repository="r")
    result = closure_decision(item, {"merged_default_branch": True})
    assert result["close"] is False
    assert result["engineering_state"] == "MERGED_PENDING_PROOF"
    assert "default_branch_proof" in result["missing_evidence"]

def test_complete_evidence_closes_issue():
    item = normalize_issue({"number": 3, "title": "bug", "engineering_state": "CONFIRMED"}, product="p", repository="r")
    evidence = {
        "reproduced_or_disposition_proven": True,
        "implementation_verified": True,
        "regression_protection": "ADDED",
        "required_ci_green": True,
        "merged_default_branch": True,
        "default_branch_proof": True,
        "product_reality": "NOT_REQUIRED",
        "evidence_refs": ["pr#9", "test::regression"],
    }
    assert closure_decision(item, evidence)["close"] is True

def test_issue_debt_is_explicit():
    a = normalize_issue({"number": 1, "title": "a", "severity": 4}, product="p", repository="r")
    b = normalize_issue({"number": 2, "title": "b", "engineering_state": "CLOSED_VERIFIED"}, product="p", repository="r")
    telemetry = issue_debt_telemetry([a, b])
    assert telemetry["open_total"] == 1
    assert telemetry["usability_or_severity_critical"] == 1
    assert telemetry["orphaned_without_next_gate"] == 0

def test_actionable_issue_must_have_disposition():
    item = normalize_issue({"number": 1, "title": "a"}, product="p", repository="r")
    item["next_gate"] = ""
    try:
        assert_no_orphaned_actionable_issues([item])
    except ValueError as exc:
        assert "issue:r#1" in str(exc)
    else:
        raise AssertionError("expected orphan guard to fail closed")
