from project_orchestrator.decision import classify_pr, next_actions


def green_pr(number: int) -> dict:
    return {
        "number": number,
        "mergeable": True,
        "required_checks": [{"status": "completed", "conclusion": "success"}],
        "blocking_reviews": 0,
        "blocking_comments": 0,
        "blocking_comments_state": "CLEAR",
    }


def test_failed_existing_pr_outranks_green_merge_and_new_work() -> None:
    snapshot = {
        "projects": [
            {
                "id": "knowledge-engine",
                "repositories": [
                    {
                        "name": "jweter/knowledge-engine-core",
                        "open_prs": [
                            {
                                "number": 10,
                                "mergeable": True,
                                "required_checks": [
                                    {"status": "completed", "conclusion": "failure"}
                                ],
                                "blocking_reviews": 0,
                                "blocking_comments": 0,
                                "blocking_comments_state": "CLEAR",
                            }
                        ],
                        "new_roadmap_slice": "new feature",
                    }
                ],
            },
            {
                "id": "everward",
                "repositories": [
                    {
                        "name": "jweter/Project-Everward",
                        "open_prs": [green_pr(20)],
                    }
                ],
            },
        ]
    }
    actions = next_actions(snapshot)
    assert len(actions) == 1
    assert actions[0].priority == "P1"
    assert actions[0].kind == "repair_pr"
    assert actions[0].ref == "PR #10"


def test_green_requires_completed_successful_required_checks() -> None:
    assert classify_pr(green_pr(1)) == "GREEN"
    pending = green_pr(1)
    pending["required_checks"][0]["status"] = "in_progress"
    pending["required_checks"][0]["conclusion"] = None
    assert classify_pr(pending) == "PENDING"


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
    snapshot = {
        "projects": [
            {
                "id": "knowledge-engine",
                "repositories": [
                    {
                        "name": "jweter/knowledge-engine-core",
                        "new_roadmap_slice": "core slice",
                    },
                    {
                        "name": "jweter/knowledge-engine-web",
                        "new_roadmap_slice": "web slice",
                    },
                ],
            },
            {
                "id": "rocksmith",
                "repositories": [
                    {
                        "name": "jweter/rocksmith-cdlc-generator",
                        "new_roadmap_slice": "rocksmith slice",
                    }
                ],
            },
        ]
    }
    actions = next_actions(snapshot)
    assert len(actions) == 2
    assert {action.project for action in actions} == {"knowledge-engine", "rocksmith"}
