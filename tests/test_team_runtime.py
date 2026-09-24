from project_orchestrator.team_runtime import (
    build_ephemeral_leases,
    build_qa_handoff,
    build_team_plan,
    build_work_contract,
    measure_team_flow,
)


def candidate(project="rocksmith", branch="feature/a", paths=None, priority=5):
    repo = {
        "knowledge-engine": "jweter/knowledge-engine-core",
        "rocksmith": "jweter/rocksmith-cdlc-generator",
        "everward": "jweter/Project-Everward",
    }[project]
    return {
        "project_id": project,
        "repository": repo,
        "base_sha": "a" * 40,
        "branch": branch,
        "title": f"{project} slice",
        "paths": paths or ["src/a.py"],
        "priority": priority,
        "acceptance": ["tests green"],
    }


def test_work_contract_is_bounded_and_has_no_merge_authority():
    contract = build_work_contract(candidate(), role="implementation-primary")
    assert contract["authority"]["edit_authorized_paths"] is True
    assert contract["authority"]["merge"] is False
    assert contract["paths"] == ["src/a.py"]


def test_ephemeral_lease_blocks_path_overlap_with_live_work():
    contract = build_work_contract(candidate(), role="implementation-primary")
    leases = build_ephemeral_leases(
        [contract],
        [{"repository": contract["repository"], "branch": "feature/other", "changed_files": ["src"]}],
    )
    assert leases[0]["state"] == "CONFLICT"
    assert leases[0]["persistent_ownership"] is False


def test_team_plan_can_fill_two_conflict_free_workers_per_product():
    work = [
        candidate("rocksmith", "feature/a", ["src/a.py"], 1),
        candidate("rocksmith", "feature/b", ["tests/test_b.py"], 2),
        candidate("everward", "feature/c", ["src/c.cpp"], 1),
    ]
    plan = build_team_plan(work)
    assert len(plan["dispatch"]) == 3
    assert plan["ready_queue_depth"]["rocksmith"] == 2
    assert plan["roles"]["merge_authority"] == "existing central merge authority only"


def test_team_plan_does_not_dispatch_conflicting_specialists():
    work = [
        candidate("rocksmith", "feature/a", ["src"], 1),
        candidate("rocksmith", "feature/b", ["src/a.py"], 2),
    ]
    plan = build_team_plan(work)
    assert len(plan["dispatch"]) == 1
    assert [x["state"] for x in plan["leases"]] == ["LEASED", "CONFLICT"]


def test_qa_handoff_requires_implementation_pass_and_pins_exact_head():
    result = {
        "contract_id": "contract-1",
        "repository": "jweter/Project-Everward",
        "branch": "feature/science",
        "exact_head_sha": "b" * 40,
        "changed_paths": ["src/a.cpp"],
        "status": "PASS",
    }
    qa = build_qa_handoff(result)
    assert qa["state"] == "READY"
    assert qa["exact_head_sha"] == "b" * 40
    assert qa["authority"]["reject_to_repair"] is True
    assert qa["authority"]["approve_for_merge"] is False


def test_failed_implementation_routes_to_repair_not_qa():
    result = {
        "contract_id": "contract-1",
        "repository": "jweter/knowledge-engine-core",
        "branch": "repair/x",
        "exact_head_sha": "c" * 40,
        "changed_paths": [],
        "status": "FAIL",
    }
    assert build_qa_handoff(result)["state"] == "REPAIR_REQUIRED"


def test_flow_measurement_reduces_concurrency_only_from_observed_rework_pressure():
    result = measure_team_flow([
        {"kind": "COLLISION"},
        {"kind": "QA_REJECTED"},
        {"kind": "MERGED"},
    ])
    assert result["recommendation"] == "REDUCE_CONCURRENCY"
    assert result["cycle_time"] == "UNKNOWN"
