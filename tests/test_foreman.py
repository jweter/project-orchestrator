import pytest

from project_orchestrator.foreman import build_foreman_plan, compact_execution_records


def inventory():
    return {
        "projects": [
            {
                "id": "knowledge-engine",
                "repositories": [
                    {"name": "jweter/knowledge-engine-core"},
                    {"name": "jweter/knowledge-engine-web"},
                    {"name": "jweter/knowledge-engine-ai"},
                ],
            },
            {
                "id": "rocksmith",
                "repositories": [{"name": "jweter/rocksmith-cdlc-generator"}],
            },
            {
                "id": "everward",
                "repositories": [{"name": "jweter/Project-Everward"}],
            },
        ]
    }


def snapshot():
    return {
        "repositories": [
            {
                "repository": "jweter/knowledge-engine-core",
                "default_head_sha": "ke-head",
                "active_work": [],
            },
            {
                "repository": "jweter/knowledge-engine-web",
                "default_head_sha": "web-head",
                "active_work": [],
            },
            {
                "repository": "jweter/knowledge-engine-ai",
                "default_head_sha": "ai-head",
                "active_work": [],
            },
            {
                "repository": "jweter/rocksmith-cdlc-generator",
                "default_head_sha": "rock-head",
                "active_work": [],
            },
            {
                "repository": "jweter/Project-Everward",
                "default_head_sha": "ever-head",
                "active_work": [],
            },
        ]
    }


def candidates():
    return [
        {
            "project_id": "knowledge-engine",
            "repository": "jweter/knowledge-engine-core",
            "base_sha": "ke-head",
            "branch": "feature/ke",
            "title": "KE slice",
            "priority": 1,
            "paths": ["src/ke.py"],
        },
        {
            "project_id": "rocksmith",
            "repository": "jweter/rocksmith-cdlc-generator",
            "base_sha": "rock-head",
            "branch": "feature/rock",
            "title": "Rocksmith slice",
            "priority": 1,
            "paths": ["src/rock.py"],
        },
        {
            "project_id": "everward",
            "repository": "jweter/Project-Everward",
            "base_sha": "ever-head",
            "branch": "feature/ever",
            "title": "Everward slice",
            "priority": 1,
            "paths": ["Source/Ever.cpp"],
        },
    ]


def test_foreman_dispatches_one_lane_for_each_product():
    plan = build_foreman_plan(inventory(), snapshot(), candidates())

    assert plan["controller"] == "Foreman"
    assert len(plan["projects"]) == 3
    assert plan["dispatchable_product_projects"] == [
        "knowledge-engine",
        "rocksmith",
        "everward",
    ]
    assert all(row["state"] == "DISPATCH_READY" for row in plan["projects"])
    assert plan["immediate_continuation"] is True
    assert plan["product_advancement_rate"] == "UNKNOWN_UNTIL_VERIFIED_MUTATION"


def test_blocked_product_does_not_consume_other_product_lanes():
    work = candidates()
    work[0]["base_sha"] = "stale-ke-head"

    plan = build_foreman_plan(inventory(), snapshot(), work)

    assert plan["dispatchable_product_projects"] == ["rocksmith", "everward"]
    states = {row["project_id"]: row["state"] for row in plan["projects"]}
    assert states["knowledge-engine"] == "BLOCKED"
    assert states["rocksmith"] == "DISPATCH_READY"
    assert states["everward"] == "DISPATCH_READY"


def test_async_wait_rotates_to_conflict_free_candidate():
    records = [
        {
            "project_id": "knowledge-engine",
            "repository": "jweter/knowledge-engine-ai",
            "status": "CI_PENDING",
            "task_key": "old-ke-ai",
        }
    ]

    plan = build_foreman_plan(
        inventory(), snapshot(), candidates(), execution_records=records
    )

    assert "knowledge-engine" in plan["waiting_lanes_rotated_away_from"]
    ke = next(
        row for row in plan["projects"] if row["project_id"] == "knowledge-engine"
    )
    assert ke["state"] == "DISPATCH_READY"
    assert ke["asynchronous_waits"][0]["task_key"] == "old-ke-ai"


def test_compaction_keeps_history_out_of_scheduler_state():
    records = [
        {
            "project_id": "rocksmith",
            "repository": "jweter/rocksmith-cdlc-generator",
            "state": "COMPLETED",
            "grant_id": f"done-{index}",
        }
        for index in range(40)
    ]
    records.extend(
        [
            {
                "project_id": "rocksmith",
                "repository": "jweter/rocksmith-cdlc-generator",
                "state": "QUARANTINED",
                "grant_id": "old-quarantine",
            },
            {
                "project_id": "rocksmith",
                "repository": "jweter/rocksmith-cdlc-generator",
                "state": "QUARANTINED",
                "grant_id": "repair-me",
                "actionable": True,
            },
            {
                "project_id": "rocksmith",
                "repository": "jweter/rocksmith-cdlc-generator",
                "status": "PREFLIGHT_RUNNING",
                "task_key": "async-gate",
            },
        ]
    )

    compacted = compact_execution_records(inventory(), records, archive_limit=5)
    rock = compacted["projects"]["rocksmith"]

    assert len(rock["archived"]) == 5
    assert compacted["archive_summary"]["rocksmith"]["total"] == 41
    assert [item["grant_id"] for item in rock["quarantined_actionable"]] == [
        "repair-me"
    ]
    assert [item["task_key"] for item in rock["waiting"]] == ["async-gate"]


def test_live_github_ownership_is_projected_not_replaced():
    live = snapshot()
    live["repositories"][3]["active_work"] = [
        {
            "branch": "existing-rock",
            "issue": 511,
            "changed_files": ["src/recognition.py"],
        }
    ]
    work = candidates()
    work[1]["paths"] = ["src/recognition.py"]

    plan = build_foreman_plan(inventory(), live, work)

    rock = next(row for row in plan["projects"] if row["project_id"] == "rocksmith")
    assert rock["state"] == "ACTIVE_EXISTING"
    assert rock["live_ownership"][0]["branch"] == "existing-rock"
    assert rock["selected_lane"] is None


def test_foreman_rejects_noncanonical_portfolio():
    broken = inventory()
    broken["projects"] = broken["projects"][:2]

    with pytest.raises(ValueError, match="exactly Knowledge Engine"):
        build_foreman_plan(broken, snapshot(), candidates())
