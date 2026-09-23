from project_orchestrator.jev_shadow import (
    build_shadow_request,
    evaluate_shadow,
    parse_shadow_response,
)


def candidate(candidate_id: str, *, state: str = "ELIGIBLE") -> dict:
    return {
        "candidate_id": candidate_id,
        "project_id": "everward",
        "repository": "jweter/Project-Everward",
        "title": f"work {candidate_id}",
        "priority": 5,
        "eligibility_state": state,
        "resource_class": "STANDARD",
    }


def test_single_legal_candidate_skips_neural_call():
    out = evaluate_shadow(state={}, candidates=[candidate("a")])
    assert out["decision"]["state"] == "DETERMINISTIC_SINGLETON"
    assert out["decision"]["selected_candidate_id"] == "a"
    assert out["decision"]["neural_call_made"] is False
    assert out["decision"]["execution_authority"] is False


def test_ineligible_candidate_is_not_exposed_to_jev():
    out = evaluate_shadow(
        state={},
        candidates=[candidate("a"), candidate("blocked", state="BLOCKED")],
    )
    assert out["deterministic_legal_candidate_ids"] == ["a"]
    assert out["decision"]["state"] == "DETERMINISTIC_SINGLETON"


def test_shadow_request_whitelists_state_and_candidates():
    request = build_shadow_request(
        state={
            "run_id": "r1",
            "current_gate": "P5",
            "secret": "must-not-leave-process",
        },
        candidates=[candidate("a"), candidate("b")],
    )
    state = request["state"]
    assert state["run_id"] == "r1"
    assert state["current_gate"] == "P5"
    assert "secret" not in state
    assert set(state["legal_candidates"]) == {"a", "b"}


def test_out_of_set_provider_choice_fails_closed():
    result = parse_shadow_response(
        {
            "answers": {
                "next_candidate": {
                    "choice": "not-legal",
                    "probabilities": {"a": 0.2, "not-legal": 0.8},
                }
            }
        },
        candidates=[candidate("a"), candidate("b")],
    )
    assert result.state == "OUT_OF_SET"
    assert result.selected_candidate_id is None
    assert result.execution_authority is False
    assert result.probabilities == {"a": 0.2}


def test_valid_response_remains_advisory():
    result = parse_shadow_response(
        {
            "answers": {
                "next_candidate": {
                    "choice": "b",
                    "probabilities": {"a": 0.25, "b": 0.75},
                }
            }
        },
        candidates=[candidate("a"), candidate("b")],
    )
    assert result.state == "SHADOW_RECOMMENDATION"
    assert result.selected_candidate_id == "b"
    assert result.execution_authority is False


def test_live_mode_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("JEV_SHADOW_LIVE", raising=False)
    monkeypatch.setenv("TYPESAFE_API_KEY", "not-used")
    out = evaluate_shadow(
        state={},
        candidates=[candidate("a"), candidate("b")],
        live=True,
        transport=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network should not run")),
    )
    assert out["decision"]["state"] == "LIVE_DISABLED"
