from __future__ import annotations

import sys

import pytest

from project_orchestrator.throughput_guard import (
    build_active_work_lease,
    build_fast_gate_plan,
    build_issue_ledger_event,
    classify_promotion_failure,
    compute_rework_kpis,
    evaluate_merge_lease_gate,
    execute_fast_gate,
    release_active_work_lease,
    render_issue_ledger_comment,
)


SHA = "a" * 40


def test_fast_gate_requires_repository_authority() -> None:
    plan = build_fast_gate_plan(
        repository="owner/repo",
        control_plane={},
        changed_paths=["src/example.py"],
    )
    assert plan["status"] == "NOT_AUTHORIZED"
    assert plan["commands"] == []
    assert plan["canonical_preflight_still_required"] is True


def test_fast_gate_selects_only_matching_authorized_commands() -> None:
    control_plane = {
        "fast_gate": {
            "enabled": True,
            "commands": [
                {"id": "python-compile", "argv": ["python", "-m", "compileall", "-q", "src"], "extensions": [".py"]},
                {"id": "json-check", "argv": ["python", "engineering/validate.py"], "extensions": [".json"]},
                {"id": "always-policy", "argv": ["python", "engineering/policy.py"], "always": True},
            ],
        }
    }
    plan = build_fast_gate_plan(
        repository="owner/repo",
        control_plane=control_plane,
        changed_paths=["src/example.py"],
    )
    assert plan["status"] == "READY"
    assert [item["id"] for item in plan["commands"]] == ["python-compile", "always-policy"]


def test_fast_gate_executes_fail_fast(tmp_path) -> None:
    plan = {
        "status": "READY",
        "commands": [
            {"id": "pass", "argv": [sys.executable, "-c", "print('ok')"]},
            {"id": "fail", "argv": [sys.executable, "-c", "raise SystemExit(3)"]},
            {"id": "never", "argv": [sys.executable, "-c", "raise SystemExit(9)"]},
        ],
    }
    result = execute_fast_gate(plan, repo_root=tmp_path)
    assert result["execution_status"] == "FAILED"
    assert [entry["id"] for entry in result["executions"]] == ["pass", "fail"]
    assert result["executions"][-1]["returncode"] == 3


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        ({"product_reality_pending": True}, "PRODUCT_REALITY_PENDING"),
        ({"review_state": "CHANGES_REQUESTED"}, "REVIEW_FAILURE"),
        ({"workflow_conclusion": "action_required", "jobs_count": 0}, "PLATFORM_GATE"),
        ({"policy_failure": True}, "POLICY_FAILURE"),
        ({"log_text": "I001 import block is un-sorted"}, "QUALITY_FAILURE"),
        ({"log_text": "FAILED tests/test_x.py::test_y AssertionError"}, "TEST_FAILURE"),
        ({"log_text": "SyntaxError: invalid syntax"}, "CODE_FAILURE"),
        ({"log_text": "runner environment limitation"}, "ENVIRONMENT_FAILURE"),
        ({"log_text": "something novel happened"}, "UNKNOWN"),
    ],
)
def test_promotion_failure_classifier(evidence, expected) -> None:
    result = classify_promotion_failure(evidence)
    assert result["failure_class"] == expected
    assert result["fail_closed"] is (expected == "UNKNOWN")


def test_active_lease_blocks_exact_head_merge_and_release_clears() -> None:
    lease = build_active_work_lease(
        repository="owner/repo",
        branch="feature/x",
        head_sha=SHA,
        actor="specialist-rocksmith",
        state="IMPLEMENTING",
        acquired_at="2026-09-10T20:00:00-05:00",
        paths=["src/x.py"],
    )
    blocked = evaluate_merge_lease_gate(
        repository="owner/repo",
        branch="feature/x",
        pr_head_sha=SHA,
        leases=[lease],
    )
    assert blocked["merge_allowed"] is False
    assert blocked["state"] == "BLOCKED_ACTIVE_LEASE"

    released = release_active_work_lease(lease, released_at="2026-09-10T20:10:00-05:00")
    clear = evaluate_merge_lease_gate(
        repository="owner/repo",
        branch="feature/x",
        pr_head_sha=SHA,
        leases=[released],
    )
    assert clear["merge_allowed"] is True
    assert clear["state"] == "LEASE_CLEAR"


def test_stale_active_lease_for_same_branch_fails_closed() -> None:
    lease = build_active_work_lease(
        repository="owner/repo",
        branch="feature/x",
        head_sha="b" * 40,
        actor="specialist",
        state="REPAIRING",
        acquired_at="2026-09-10T20:00:00-05:00",
    )
    result = evaluate_merge_lease_gate(
        repository="owner/repo",
        branch="feature/x",
        pr_head_sha=SHA,
        leases=[lease],
    )
    assert result["merge_allowed"] is False
    assert result["state"] == "STALE_LEASE_REQUIRES_REVALIDATION"


def test_issue_ledger_event_renders_exact_engineering_state() -> None:
    event = build_issue_ledger_event(
        issue_number=560,
        promotion_state="PREFLIGHT_UNVERIFIED",
        next_gate="repair first failing assertion then rerun full preflight",
        event_at="2026-09-10T20:00:00-05:00",
        branch="orchestrator/issue-560",
        head_sha=SHA,
        verification="preflight FAILED: 2 tests failed, 1845 passed",
        failure_class="TEST_FAILURE",
        product_reality="pending only for packaged Windows acceptance",
    )
    comment = render_issue_ledger_comment(event)
    assert "PREFLIGHT_UNVERIFIED" in comment
    assert SHA in comment
    assert "TEST_FAILURE" in comment
    assert "Next gate" in comment
    assert event["event_id"] in comment


def test_rework_kpis_are_measured_only_from_explicit_records() -> None:
    result = compute_rework_kpis(
        [
            {
                "preflight_attempts": 1,
                "merge_race": False,
                "merged": True,
                "post_merge_correctness_hotfix": False,
                "implementation_started_at": "2026-09-10T10:00:00+00:00",
                "merged_at": "2026-09-10T10:10:00+00:00",
            },
            {
                "preflight_attempts": 2,
                "merge_race": True,
                "merged": True,
                "post_merge_correctness_hotfix": True,
                "implementation_started_at": "2026-09-10T11:00:00+00:00",
                "merged_at": "2026-09-10T11:20:00+00:00",
            },
        ]
    )
    assert result["first_pass_preflight_rate"]["value"] == 0.5
    assert result["merge_race_count"]["value"] == 1
    assert result["post_merge_correctness_hotfix_rate"]["value"] == 0.5
    assert result["median_implementation_to_merge_seconds"]["value"] == 900.0


def test_rework_kpis_keep_missing_evidence_unknown() -> None:
    result = compute_rework_kpis([])
    assert all(metric["status"] == "UNKNOWN" for key, metric in result.items() if key != "schema_version")


def test_merged_record_requires_explicit_hotfix_evidence() -> None:
    with pytest.raises(ValueError, match="post_merge_correctness_hotfix"):
        compute_rework_kpis([{"merged": True}])
