import pytest

from project_orchestrator.branch_reconciliation import build_reconciliation_plan


def evidence(**overrides):
    base = {
        "repository": "jweter/Project-Everward",
        "default_branch": "main",
        "default_head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "branch": "orchestrator/fix-it-repair-kernel",
        "branch_head_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "ahead_by": 23,
        "behind_by": 34,
        "changed_files": ["src/simulation/fix_it.hpp", "tests/fix_it_tests.cpp"],
        "roadmap_authorized": True,
    }
    base.update(overrides)
    return base


def test_diverged_authorized_branch_is_modernization_candidate():
    plan = build_reconciliation_plan(evidence())
    assert plan["state"] == "DIVERGED_VALUABLE"
    assert plan["modernization_ready"] is True
    assert plan["recommended_action"] == "MODERNIZE_ON_CURRENT_DEFAULT"
    assert plan["promotion_state"] == "IMPLEMENTING"
    assert "fresh modernization branch" in plan["steps"][0]


def test_unknown_roadmap_authority_fails_closed():
    plan = build_reconciliation_plan(evidence(roadmap_authorized=None))
    assert plan["modernization_ready"] is False
    assert plan["promotion_state"] == "BLOCKED"
    assert "roadmap authorization is unverified" in plan["blockers"]


def test_existing_open_pr_prevents_competing_modernization():
    plan = build_reconciliation_plan(evidence(open_pr=491))
    assert plan["modernization_ready"] is False
    assert any("open PR" in reason for reason in plan["blockers"])


def test_current_unique_branch_can_move_to_verification():
    plan = build_reconciliation_plan(evidence(ahead_by=2, behind_by=0))
    assert plan["state"] == "CURRENT_UNMERGED"
    assert plan["promotable"] is True
    assert plan["promotion_state"] == "PREFLIGHT_UNVERIFIED"


def test_stale_branch_without_unique_work_is_hygiene_review_only():
    plan = build_reconciliation_plan(evidence(ahead_by=0, behind_by=12, roadmap_authorized=None))
    assert plan["state"] == "STALE_NO_UNIQUE_WORK"
    assert plan["recommended_action"] == "BRANCH_HYGIENE_REVIEW"
    assert plan["promotion_state"] == "NOT_APPLICABLE"


def test_default_branch_is_never_a_candidate():
    with pytest.raises(ValueError, match="default branch"):
        build_reconciliation_plan(evidence(branch="main"))


def test_negative_divergence_is_rejected():
    with pytest.raises(ValueError, match="behind_by"):
        build_reconciliation_plan(evidence(behind_by=-1))


def test_product_reality_blocks_only_dependent_candidate():
    plan = build_reconciliation_plan(evidence(product_reality_dependency=True, product_reality_verified=False))
    assert plan["modernization_ready"] is False
    assert any("Product Reality" in reason for reason in plan["blockers"])
