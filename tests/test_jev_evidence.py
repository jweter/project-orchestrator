import json

import pytest

from project_orchestrator.jev_evidence import (
    append_jsonl,
    attach_outcome,
    decision_record,
    evaluate_records,
    load_jsonl,
    replay,
)


def shadow(choice="b", state="SHADOW_RECOMMENDATION"):
    return {
        "deterministic_legal_candidate_ids": ["a", "b"],
        "decision": {
            "state": state,
            "selected_candidate_id": choice,
            "probabilities": {"a": 0.2, "b": 0.8},
            "neural_call_made": True,
            "execution_authority": False,
        },
    }


def test_record_is_advisory_and_bounded_to_legal_set():
    record = decision_record(
        run_id="run-1", project_id="everward",
        deterministic_choice="a", shadow=shadow()
    )
    assert record["deterministic_choice"] == "a"
    assert record["shadow_choice"] == "b"
    assert record["execution_authority"] is False

    with pytest.raises(ValueError, match="legal candidate"):
        decision_record(
            run_id="bad", project_id="everward",
            deterministic_choice="outside", shadow=shadow()
        )


def test_outcome_requires_recorded_candidate():
    record = decision_record(
        run_id="run-1", project_id="rocksmith",
        deterministic_choice="a", shadow=shadow()
    )
    with pytest.raises(ValueError, match="recorded legal set"):
        attach_outcome(record, candidate_id="outside", outcome="VERIFIED_ADVANCEMENT")


def test_ledger_round_trip_jsonl(tmp_path):
    path = tmp_path / "jev.jsonl"
    record = decision_record(
        run_id="run-1", project_id="knowledge-engine",
        deterministic_choice="a", shadow=shadow()
    )
    append_jsonl(path, record)
    loaded = load_jsonl(path)
    assert len(loaded) == 1
    assert loaded[0]["run_id"] == "run-1"
    assert "TYPESAFE_API_KEY" not in json.dumps(loaded)


def test_evaluator_scores_disagreement_against_verified_outcome():
    first = attach_outcome(
        decision_record(
            run_id="shadow-wins", project_id="everward",
            deterministic_choice="a", shadow=shadow("b")
        ),
        candidate_id="b", outcome="VERIFIED_ADVANCEMENT",
        verification_ref="ci:123",
    )
    second = attach_outcome(
        decision_record(
            run_id="det-wins", project_id="rocksmith",
            deterministic_choice="a", shadow=shadow("b")
        ),
        candidate_id="a", outcome="VERIFIED_ADVANCEMENT",
        verification_ref="ci:124",
    )
    report = evaluate_records([first, second])
    assert report["comparable_decisions"] == 2
    assert report["disagreements"] == 2
    assert report["shadow_better"] == 1
    assert report["deterministic_better"] == 1
    assert report["promotion_authority"] is False
    assert report["promotion_ready"] is False


def test_replay_is_descriptive_not_authoritative():
    record = attach_outcome(
        decision_record(
            run_id="r", project_id="knowledge-engine",
            deterministic_choice="a", shadow=shadow("b")
        ),
        candidate_id="b", outcome="VERIFIED_ADVANCEMENT",
    )
    result = replay([record])
    assert result["rows"][0]["shadow_matched_verified_advancement"] is True
    assert result["summary"]["promotion_ready"] is False
