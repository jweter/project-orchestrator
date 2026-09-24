from project_orchestrator.decision_outcome import attribute, decision_id, summarize


def record():
    return {
        "decision_id": decision_id("run-1", "repair-selection", "rocksmith"),
        "decision_class": "repair-selection",
        "project_id": "rocksmith",
        "legal_candidate_ids": ["a", "b"],
        "deterministic_choice": "a",
        "shadow_choice": "b",
    }


def test_attributes_verified_outcome_with_cost_metrics():
    row = attribute(record(), {
        "candidate_id": "b", "state": "MERGED", "elapsed_seconds": 42,
        "retries": 1, "verification_churn": 2, "verification_ref": "ci:44",
    })
    assert row["state"] == "VERIFIED_ADVANCEMENT"
    assert row["shadow_selected"] is True
    assert row["deterministic_selected"] is False
    assert row["elapsed_seconds"] == 42


def test_summary_is_per_decision_class_and_non_authoritative():
    rows = [
        attribute(record(), {"candidate_id": "b", "state": "MERGED", "elapsed_seconds": 10}),
        attribute(record(), {"candidate_id": "a", "state": "CI_FAILED", "failure_class": "TEST_FAILURE", "retries": 2}),
    ]
    report = summarize([row for row in rows if row])
    bucket = report["by_decision_class"]["repair-selection"]
    assert bucket["outcomes"] == 2
    assert bucket["verified_advancements"] == 1
    assert bucket["shadow_verified"] == 1
    assert bucket["mean_retries"] == 1
    assert report["promotion_authority"] is False
    assert report["policy_mutation_authority"] is False


def test_ignores_nonterminal_and_out_of_set_results():
    assert attribute(record(), {"candidate_id": "outside", "state": "MERGED"}) is None
    assert attribute(record(), {"candidate_id": "a", "state": "CI_PENDING"}) is None
