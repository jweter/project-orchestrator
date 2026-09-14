from __future__ import annotations

import pytest

from project_orchestrator.lvn_dispatch import (
    LVN_REQUIRED_MARKER,
    LvnDispatchError,
    build_lvn_request,
    classify_ci_state,
    select_lvn_candidates,
)


SHA_A = "a" * 40
SHA_B = "b" * 40


def checks(conclusion="SUCCESS", status="COMPLETED"):
    return [{"name": "Quality", "status": status, "conclusion": conclusion, "required": True}]


def candidate(**overrides):
    value = {
        "repository": "jweter/knowledge-engine-core",
        "head_repository": "jweter/knowledge-engine-core",
        "pr_number": 497,
        "head_sha": SHA_A,
        "branch": "scientist/496-h1-portable-tests",
        "priority": 5,
        "open": True,
        "draft": False,
        "body": LVN_REQUIRED_MARKER,
        "labels": [],
        "checks": checks(),
    }
    value.update(overrides)
    return value


def test_ci_classifier_fails_closed_on_missing_or_unknown_checks():
    assert classify_ci_state([]) == "PENDING"
    assert classify_ci_state([{"status": "COMPLETED", "conclusion": "MYSTERY"}]) == "PENDING"
    assert classify_ci_state(checks(status="IN_PROGRESS", conclusion="")) == "PENDING"


def test_ci_classifier_distinguishes_failed_and_green():
    assert classify_ci_state(checks(conclusion="FAILURE")) == "FAILED"
    assert classify_ci_state(checks(conclusion="SUCCESS")) == "GREEN"


def test_explicit_green_promotion_candidate_is_selected_before_failed_reproduction():
    failed = candidate(
        pr_number=498,
        head_sha=SHA_B,
        body="",
        checks=checks(conclusion="FAILURE"),
    )
    result = select_lvn_candidates(candidates=[failed, candidate()], max_dispatch=1)
    assert result["selected"][0]["pr_number"] == 497
    assert result["selected"][0]["lane"] == "PROMOTION_GATE"


def test_green_pr_without_explicit_contract_is_not_speculatively_selected():
    result = select_lvn_candidates(candidates=[candidate(body="")])
    assert result["state"] == "NO_DISPATCH"
    assert result["decisions"][0]["reason"] == "green_pr_without_explicit_lvn_contract"


def test_pending_explicit_candidate_waits_for_cloud_ci():
    result = select_lvn_candidates(
        candidates=[candidate(checks=checks(status="IN_PROGRESS", conclusion=""))]
    )
    assert result["state"] == "NO_DISPATCH"
    assert result["decisions"][0]["state"] == "WAIT_CI"


def test_exact_pr_sha_evidence_deduplicates_but_head_change_reopens_gate():
    old_key = f"jweter/knowledge-engine-core#497@{SHA_A}"
    old = select_lvn_candidates(candidates=[candidate()], verified_keys=[old_key])
    assert old["state"] == "NO_DISPATCH"
    assert old["decisions"][0]["state"] == "SATISFIED"

    changed = select_lvn_candidates(candidates=[candidate(head_sha=SHA_B)], verified_keys=[old_key])
    assert changed["state"] == "DISPATCH_READY"
    assert changed["selected"][0]["head_sha"] == SHA_B


def test_retry_backoff_prevents_hot_loop_after_environment_failure():
    key = f"jweter/knowledge-engine-core#497@{SHA_A}"
    result = select_lvn_candidates(candidates=[candidate()], deferred_keys=[key])
    assert result["state"] == "NO_DISPATCH"
    assert result["decisions"][0]["reason"] == "exact_head_in_retry_backoff"


def test_closed_draft_cross_repo_and_unauthorized_candidates_fail_closed():
    cases = [
        candidate(open=False),
        candidate(draft=True),
        candidate(head_repository="someone/fork"),
    ]
    result = select_lvn_candidates(candidates=cases)
    assert result["state"] == "NO_DISPATCH"
    reasons = {item["reason"] for item in result["decisions"]}
    assert "pull_request_closed" in reasons
    assert "pull_request_is_draft" in reasons
    assert "cross_repository_head_not_authorized" in reasons

    unauthorized = select_lvn_candidates(
        candidates=[candidate()], allowed_repositories=["jweter/knowledge-engine-ai"]
    )
    assert unauthorized["state"] == "NO_DISPATCH"
    assert unauthorized["decisions"][0]["reason"] == "repository_not_authorized"


def test_capacity_bounds_one_windows_actor_and_defers_extra_work():
    second = candidate(pr_number=499, head_sha=SHA_B)
    result = select_lvn_candidates(candidates=[candidate(), second], max_dispatch=1)
    assert len(result["selected"]) == 1
    assert result["capacity"]["exclusive_resource"] == "LVN_WINDOWS"
    assert any(item["reason"] == "lvn_capacity_exhausted" for item in result["decisions"])


def test_priority_is_preserved_before_lane_rank():
    p0_failed = candidate(
        pr_number=498,
        head_sha=SHA_B,
        priority=0,
        body="",
        checks=checks(conclusion="FAILURE"),
    )
    p5_promotion = candidate(priority=5)
    result = select_lvn_candidates(candidates=[p5_promotion, p0_failed], max_dispatch=1)
    assert result["selected"][0]["pr_number"] == 498


def test_lvn_request_is_deterministic_exact_head_and_non_promoting():
    selected = select_lvn_candidates(candidates=[candidate()])["selected"][0]
    first = build_lvn_request(selected=selected)
    second = build_lvn_request(selected=selected)
    assert first == second
    assert first["head_sha"] == SHA_A
    assert first["read_only"] is True
    assert first["exact_head_required"] is True
    assert first["promotion_authority"] == "NONE"
    assert first["requested_checks"] == ["canonical-preflight"]


def test_malformed_sha_is_rejected_instead_of_guessed():
    with pytest.raises(LvnDispatchError, match="40-character"):
        select_lvn_candidates(candidates=[candidate(head_sha="abc")])
