from project_orchestrator.github_flow_events import build_github_flow_record

SHA = "a" * 40


def pr(**overrides):
    value = {
        "number": 78,
        "head": {"sha": SHA},
        "created_at": "2026-09-16T09:13:46Z",
        "merged_at": "2026-09-16T10:00:00Z",
    }
    value.update(overrides)
    return value


def check(name, *, status="completed", conclusion="success", started_at="2026-09-16T09:14:00Z", completed_at="2026-09-16T09:20:00Z"):
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "started_at": started_at,
        "completed_at": completed_at,
    }


def test_collects_only_deterministic_github_events():
    record = build_github_flow_record(
        repository="jweter/project-orchestrator",
        pr=pr(),
        head_checks=[check("CI")],
        merge_actor="JEREMY_MANUAL_MERGE",
    )
    events = record["events"]
    assert events["pr_opened_at"] == "2026-09-16T09:13:46Z"
    assert events["first_ci_started_at"] == "2026-09-16T09:14:00Z"
    assert events["first_ci_green_at"] == "2026-09-16T09:20:00Z"
    assert events["latest_head_ci_green_at"] == "2026-09-16T09:20:00Z"
    assert events["merged_at"] == "2026-09-16T10:00:00Z"
    assert events["ready_to_merge_at"] is None
    assert record["merge_actor"] == "JEREMY_MANUAL_MERGE"


def test_pending_or_failed_exact_head_never_fabricates_green():
    record = build_github_flow_record(
        repository="jweter/project-orchestrator",
        pr=pr(merged_at=None),
        head_checks=[
            check("lint"),
            check("tests", status="in_progress", conclusion=None, completed_at=None),
        ],
    )
    assert record["events"]["first_ci_green_at"] is None
    assert record["events"]["latest_head_ci_green_at"] is None
    assert record["events"]["merged_at"] is None
    assert record["merge_actor"] == "UNKNOWN"


def test_multiple_green_checks_use_latest_completion_for_exact_head_green():
    record = build_github_flow_record(
        repository="jweter/project-orchestrator",
        pr=pr(),
        head_checks=[
            check("lint", completed_at="2026-09-16T09:18:00Z"),
            check("tests", started_at="2026-09-16T09:15:00Z", completed_at="2026-09-16T09:25:00Z"),
        ],
    )
    assert record["events"]["first_ci_started_at"] == "2026-09-16T09:14:00Z"
    assert record["events"]["latest_head_ci_green_at"] == "2026-09-16T09:25:00Z"


def test_empty_check_set_remains_unknown_not_green():
    record = build_github_flow_record(
        repository="jweter/project-orchestrator",
        pr=pr(),
        head_checks=[],
    )
    assert record["events"]["first_ci_started_at"] is None
    assert record["events"]["first_ci_green_at"] is None
    assert record["events"]["latest_head_ci_green_at"] is None
