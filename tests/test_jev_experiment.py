from project_orchestrator.jev_experiment import build_experiment_report


def test_report_does_not_mistake_pending_request_for_jev_performance():
    report = build_experiment_report([{
        "shadow_state": "SHADOW_PENDING",
        "neural_call_made": False,
        "outcome": None,
    }])
    assert report["baseline_state"] == "NO_LIVE_JEV_OBSERVATIONS"
    assert report["next_gate"] == "ENABLE_EXPLICIT_LIVE_SHADOW_PROVIDER"
    assert report["promotion_authority"] is False


def test_live_observation_waits_for_real_outcome():
    report = build_experiment_report([{
        "shadow_state": "SHADOW_RECOMMENDATION",
        "neural_call_made": True,
        "outcome": None,
    }])
    assert report["baseline_state"] == "LIVE_OBSERVATION_PENDING"
    assert report["next_gate"] == "WAIT_FOR_TERMINAL_OUTCOMES"


def test_completed_live_observation_unlocks_evaluation_gate():
    report = build_experiment_report([{
        "run_id": "r1",
        "project_id": "everward",
        "legal_candidate_ids": ["a", "b"],
        "deterministic_choice": "a",
        "shadow_choice": "b",
        "shadow_state": "SHADOW_RECOMMENDATION",
        "neural_call_made": True,
        "outcome": {"candidate_id": "b", "state": "VERIFIED_ADVANCEMENT"},
    }])
    assert report["baseline_state"] == "MEASURED_LIVE_BASELINE"
    assert report["next_gate"] == "EVALUATE_CALIBRATION_AND_ROUTING_QUALITY"
    assert report["comparison"]["shadow_better"] == 1
