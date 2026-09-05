from hypothesis import given, strategies as st

from project_orchestrator.decision import classify_pr, next_actions


MERGEABLE_STATES = {"RESOLVED", "CONFIRMED_NONE"}


def green_pr(number: int) -> dict:
    return {
        "number": number,
        "mergeable": True,
        "required_checks": [{"status": "completed", "conclusion": "success"}],
        "required_checks_state": "RESOLVED",
        "blocking_reviews": 0,
        "blocking_comments": 0,
        "blocking_comments_state": "CLEAR",
    }


def test_failed_existing_pr_outranks_green_merge_and_new_work() -> None:
    snapshot = {
        "projects": [
            {"id": "knowledge-engine", "repositories": [{"name": "jweter/knowledge-engine-core", "open_prs": [{
                "number": 10, "mergeable": True,
                "required_checks": [{"status": "completed", "conclusion": "failure"}],
                "required_checks_state": "RESOLVED",
                "blocking_reviews": 0, "blocking_comments": 0, "blocking_comments_state": "CLEAR",
            }], "new_roadmap_slice": "new feature"}]},
            {"id": "everward", "repositories": [{"name": "jweter/Project-Everward", "open_prs": [green_pr(20)]}]},
        ]
    }
    actions = next_actions(snapshot)
    assert len(actions) == 1
    assert actions[0].priority == "P1"
    assert actions[0].kind == "repair_pr"


def test_green_requires_completed_successful_required_checks() -> None:
    assert classify_pr(green_pr(1)) == "GREEN"
    pending = green_pr(1)
    pending["required_checks"][0]["status"] = "in_progress"
    pending["required_checks"][0]["conclusion"] = None
    assert classify_pr(pending) == "PENDING"


def test_unrecognized_or_missing_required_check_state_fails_closed() -> None:
    for state in [None, "UNKNOWN", "MISSING", "UNCERTAIN", "FUTURE_STATE", "resovled"]:
        pr = green_pr(1)
        if state is None:
            pr.pop("required_checks_state")
        else:
            pr["required_checks_state"] = state
        assert classify_pr(pr) == "UNCERTAIN"


@given(st.text().filter(lambda value: value not in MERGEABLE_STATES))
def test_arbitrary_unknown_required_check_state_never_becomes_green(state: str) -> None:
    pr = green_pr(1)
    pr["required_checks_state"] = state
    assert classify_pr(pr) == "UNCERTAIN"


@given(st.sampled_from(["failure", "cancelled", "timed_out"]))
def test_any_terminal_required_check_failure_stays_failed(conclusion: str) -> None:
    pr = green_pr(1)
    pr["required_checks"][0]["conclusion"] = conclusion
    assert classify_pr(pr) == "FAILED"


def test_changes_requested_review_blocks_even_when_checks_are_green() -> None:
    pr = green_pr(1)
    pr["blocking_reviews"] = 1
    assert classify_pr(pr) == "BLOCKED"


def test_unknown_comment_gate_fails_closed() -> None:
    pr = green_pr(1)
    pr["blocking_comments"] = None
    pr["blocking_comments_state"] = "UNKNOWN"
    assert classify_pr(pr) == "UNCERTAIN"


def test_new_work_budget_is_one_slice_per_independent_project() -> None:
    snapshot = {"projects": [
        {"id": "knowledge-engine", "repositories": [
            {"name": "jweter/knowledge-engine-core", "new_roadmap_slice": "core slice"},
            {"name": "jweter/knowledge-engine-web", "new_roadmap_slice": "web slice"},
        ]},
        {"id": "rocksmith", "repositories": [{"name": "jweter/rocksmith-cdlc-generator", "new_roadmap_slice": "rocksmith slice"}]},
    ]}
    actions = next_actions(snapshot)
    assert len(actions) == 2
    assert {action.project for action in actions} == {"knowledge-engine", "rocksmith"}


def test_confirmed_no_required_checks_can_still_be_green() -> None:
    pr = green_pr(1)
    pr["required_checks"] = []
    pr["required_checks_state"] = "CONFIRMED_NONE"
    assert classify_pr(pr) == "GREEN"
