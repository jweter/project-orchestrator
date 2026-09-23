from project_orchestrator.jev_reconciliation import (
    reconcile_record,
    reconcile_records,
    records_from_foreman_plan,
)


def shadow():
    return {
        "deterministic_legal_candidate_ids": ["lane-a", "lane-b"],
        "decision": {
            "state": "SHADOW_RECOMMENDATION",
            "selected_candidate_id": "lane-b",
            "probabilities": {"lane-a": 0.3, "lane-b": 0.7},
            "neural_call_made": True,
            "execution_authority": False,
        },
    }


def plan():
    return {
        "projects": [
            {"project_id": "everward", "selected_lane": {"lane_id": "lane-a"}},
            {"project_id": "rocksmith", "selected_lane": {"lane_id": "only"}},
        ],
        "jev_shadow": {
            "everward": shadow(),
            "rocksmith": {
                "deterministic_legal_candidate_ids": ["only"],
                "decision": {
                    "state": "DETERMINISTIC_SINGLETON",
                    "selected_candidate_id": "only",
                    "neural_call_made": False,
                },
            },
        },
    }


def test_foreman_plan_emits_only_meaningful_shadow_decisions():
    records = records_from_foreman_plan(plan(), run_id="run-42")
    assert len(records) == 1
    assert records[0]["project_id"] == "everward"
    assert records[0]["deterministic_choice"] == "lane-a"
    assert records[0]["shadow_choice"] == "lane-b"


def test_worker_verified_result_closes_record():
    record = records_from_foreman_plan(plan(), run_id="run-42")[0]
    closed = reconcile_record(
        record,
        {"lane_id": "lane-a", "state": "MERGED", "head_sha": "abc123"},
    )
    assert closed["outcome"]["state"] == "VERIFIED_ADVANCEMENT"
    assert closed["outcome"]["candidate_id"] == "lane-a"
    assert closed["outcome"]["verification_ref"] == "abc123"


def test_failure_is_recorded_but_not_called_advancement():
    record = records_from_foreman_plan(plan(), run_id="run-42")[0]
    closed = reconcile_record(
        record,
        {"lane_id": "lane-a", "state": "TEST_FAILURE", "head_sha": "bad"},
    )
    assert closed["outcome"]["state"] == "FAILED"


def test_pending_and_unrelated_results_do_not_close_record():
    record = records_from_foreman_plan(plan(), run_id="run-42")[0]
    assert reconcile_record(record, {"lane_id": "lane-a", "state": "CI_PENDING"})["outcome"] is None
    assert reconcile_record(record, {"lane_id": "other", "state": "MERGED"})["outcome"] is None


def test_reconcile_records_matches_candidate_identity():
    records = records_from_foreman_plan(plan(), run_id="run-42")
    updated = reconcile_records(
        records,
        [
            {"lane_id": "unrelated", "state": "MERGED"},
            {"lane_id": "lane-a", "state": "PRODUCT_REALITY_VERIFIED", "verification_ref": "pr:77"},
        ],
    )
    assert updated[0]["outcome"]["state"] == "VERIFIED_ADVANCEMENT"
    assert updated[0]["outcome"]["verification_ref"] == "pr:77"
