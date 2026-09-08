import pytest

from project_orchestrator.branch_lifecycle import classify_branch_lifecycle, classify_portfolio_branches


def evidence(**overrides):
    base = {
        "repository": "jweter/Project-Everward",
        "default_branch": "main",
        "default_head_sha": "a" * 40,
        "branch": "orchestrator/old-slice",
        "branch_head_sha": "b" * 40,
        "ahead_by": 0,
        "behind_by": 10,
        "changed_files": [],
        "roadmap_authorized": None,
        "disposition_certain": False,
    }
    base.update(overrides)
    return base


def test_merged_branch_deletes_only_with_proven_disposition_and_no_unique_work():
    result = classify_branch_lifecycle(evidence(merged_pr=200, disposition_certain=True))
    assert result["lifecycle_state"] == "MERGED_DISPOSABLE"
    assert result["safe_to_delete"] is True
    assert result["next_action"] == "DELETE_BRANCH"


def test_merged_branch_with_unique_work_is_not_deleted():
    result = classify_branch_lifecycle(evidence(
        merged_pr=200, disposition_certain=True, ahead_by=2, behind_by=4, roadmap_authorized=True,
    ))
    assert result["safe_to_delete"] is False
    assert result["lifecycle_state"] == "STALE_VALUABLE"


def test_open_pr_owns_branch_and_blocks_cleanup():
    result = classify_branch_lifecycle(evidence(open_pr=31, disposition_certain=True))
    assert result["lifecycle_state"] == "PR_OWNED"
    assert result["safe_to_delete"] is False


def test_release_branch_is_preserved():
    result = classify_branch_lifecycle(evidence(release_or_hotfix_required=True, disposition_certain=True))
    assert result["lifecycle_state"] == "RELEASE_OR_HOTFIX_REQUIRED"
    assert result["next_action"] == "PRESERVE"


def test_authorized_diverged_work_enters_modernization_queue():
    result = classify_branch_lifecycle(evidence(
        ahead_by=5, behind_by=20, changed_files=["src/simulation/fix_it.cpp"],
        roadmap_authorized=True,
    ))
    assert result["lifecycle_state"] == "STALE_VALUABLE"
    assert result["queue_for_modernization"] is True
    assert result["safe_to_delete"] is False


def test_unknown_disposition_fails_closed():
    result = classify_branch_lifecycle(evidence())
    assert result["lifecycle_state"] == "UNCERTAIN"
    assert result["safe_to_delete"] is False


def test_portfolio_report_separates_cleanup_and_recovery():
    result = classify_portfolio_branches([
        evidence(branch="merged", merged_pr=1, disposition_certain=True),
        evidence(branch="valuable", ahead_by=3, behind_by=9, changed_files=["x.py"], roadmap_authorized=True),
    ])
    assert [item["branch"] for item in result["deletion_candidates"]] == ["merged"]
    assert [item["branch"] for item in result["modernization_queue"]] == ["valuable"]


def test_default_branch_is_rejected_by_reconciliation_guard():
    with pytest.raises(ValueError, match="default branch"):
        classify_branch_lifecycle(evidence(branch="main"))
