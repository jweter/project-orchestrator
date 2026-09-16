import pytest

from project_orchestrator.agent_eval import (
    AgentEvalError,
    attempt_to_events,
    build_agent_eval_report,
    record_attempt,
    validate_attempt,
    validate_suite,
)
from project_orchestrator.kpi_telemetry import load_events


def suite():
    return validate_suite(
        {
            "schema_version": 1,
            "suite_id": "suite-v1",
            "purpose": "deterministic regression benchmark",
            "advisory_only": True,
            "product_reality_may_be_inferred": False,
            "canonical_kpi_ledger": "engineering/kpi-events.jsonl",
            "tasks": [
                {
                    "task_id": "task-a",
                    "title": "Task A",
                    "repository": "jweter/project-orchestrator",
                    "acceptance_kind": "DETERMINISTIC",
                    "prompt": "repair the deterministic regression",
                    "baseline_ref": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "fault_fixture_ref": "engineering/agent-eval-fixtures/task-a.patch",
                    "measurement_contract_ref": "tests/test_a.py::test_regression",
                    "verification_commands": [
                        "python -m pytest -q tests/test_a.py::test_regression"
                    ],
                    "required_stages": ["PREFLIGHT", "PR_CI"],
                }
            ],
        }
    )


def attempt(
    *,
    evaluation_run_id="eval-1",
    agent_id="agent",
    config="config-a",
    baseline="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    preflight=("PASS",),
    ci=("PASS",),
    repairs=(),
    interventions=(),
    acceptance="PASS",
    measured_cost=None,
):
    checks = []
    minute = 1
    for number, outcome in enumerate(preflight, start=1):
        checks.append(
            {
                "stage": "PREFLIGHT",
                "attempt": number,
                "observed_at": f"2026-09-16T10:{minute:02d}:00Z",
                "outcome": outcome,
                "evidence_refs": [f"preflight:{number}"],
            }
        )
        minute += 1
    for number, outcome in enumerate(ci, start=1):
        checks.append(
            {
                "stage": "PR_CI",
                "attempt": number,
                "observed_at": f"2026-09-16T10:{minute:02d}:00Z",
                "outcome": outcome,
                "evidence_refs": [f"ci:{number}"],
            }
        )
        minute += 1

    repair_rows = []
    for number, failure_class in enumerate(repairs, start=1):
        repair_rows.append(
            {
                "attempt": number,
                "observed_at": f"2026-09-16T10:{minute:02d}:00Z",
                "failure_class": failure_class,
                "evidence_refs": [f"repair:{number}"],
            }
        )
        minute += 1

    intervention_rows = []
    for index, reason in enumerate(interventions, start=1):
        intervention_rows.append(
            {
                "observed_at": f"2026-09-16T10:{minute:02d}:00Z",
                "reason": reason,
                "evidence_refs": [f"human:{index}"],
            }
        )
        minute += 1

    acceptance_row = None
    if acceptance is not None:
        acceptance_row = {
            "observed_at": f"2026-09-16T10:{minute:02d}:00Z",
            "outcome": acceptance,
            "evidence_refs": ["acceptance"],
        }

    return {
        "schema_version": 1,
        "suite_id": "suite-v1",
        "task_id": "task-a",
        "evaluation_run_id": evaluation_run_id,
        "agent_id": agent_id,
        "agent_config_id": config,
        "actor": "EXTERNAL_AUTOMATION",
        "repository": "jweter/project-orchestrator",
        "baseline_ref": baseline,
        "measurement_contract_ref": "tests/test_a.py::test_regression",
        "started_at": "2026-09-16T10:00:00Z",
        "checks": checks,
        "repairs": repair_rows,
        "human_interventions": intervention_rows,
        "acceptance": acceptance_row,
        "measured_cost": measured_cost,
        "evidence_refs": ["issue:82"],
    }


def report(events):
    return build_agent_eval_report(
        events,
        suite(),
        start="2026-09-16T09:00:00Z",
        end="2026-09-16T11:00:00Z",
    )


def first_agent(result):
    return result["cohorts"][0]["agents"][0]


def test_suite_is_deterministic_advisory_and_never_product_reality():
    value = suite()
    assert value["advisory_only"] is True
    assert value["product_reality_may_be_inferred"] is False
    assert value["canonical_kpi_ledger"] == "engineering/kpi-events.jsonl"


def test_product_reality_task_is_rejected():
    value = {
        "schema_version": 1,
        "suite_id": "bad",
        "purpose": "bad",
        "advisory_only": True,
        "product_reality_may_be_inferred": False,
        "canonical_kpi_ledger": "engineering/kpi-events.jsonl",
        "tasks": [
            {
                "task_id": "visual",
                "title": "visual",
                "repository": "jweter/project-orchestrator",
                "acceptance_kind": "PRODUCT_REALITY",
                "prompt": "looks good?",
                "baseline_ref": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "fault_fixture_ref": "engineering/agent-eval-fixtures/visual.patch",
                "measurement_contract_ref": "human",
                "verification_commands": ["echo impossible"],
                "required_stages": ["PREFLIGHT"],
            }
        ],
    }
    with pytest.raises(AgentEvalError, match="deterministic acceptance only"):
        validate_suite(value)


def test_attempt_contract_mismatch_fails_closed():
    value = attempt()
    value["measurement_contract_ref"] = "different"
    with pytest.raises(AgentEvalError, match="measurement_contract_ref"):
        validate_attempt(value, suite())


def test_attempt_becomes_canonical_kpi_events():
    events = attempt_to_events(attempt(), suite())
    assert events[0]["metadata"]["agent_eval_signal"] == "START"
    assert {event["entity_type"] for event in events} == {"issue", "ci"}
    assert any(event["metadata"].get("stage") == "PREFLIGHT" for event in events)
    assert any(event["metadata"].get("stage") == "PR_CI" for event in events)
    assert events[-1]["to_state"] == "ACCEPTED"
    assert all(event["project"] == "portfolio" for event in events)


def test_record_attempt_uses_existing_append_only_kpi_ledger(tmp_path):
    ledger = tmp_path / "kpi-events.jsonl"
    recorded = record_attempt(attempt(), suite(), ledger=ledger)
    loaded = load_events(ledger)
    assert [event["event_id"] for event in loaded] == [event["event_id"] for event in recorded]
    assert ledger.exists()
    assert not (tmp_path / "agent-eval-events.jsonl").exists()


def test_report_measures_first_pass_success_without_guessing():
    result = report(attempt_to_events(attempt(), suite()))
    agent = first_agent(result)
    assert result["status"] == "MEASURED"
    assert agent["deterministic_task_pass_rate"]["value"] == 1.0
    assert agent["first_pass_preflight_rate"]["value"] == 1.0
    assert agent["first_pass_ci_rate"]["value"] == 1.0
    assert agent["median_repair_iterations"]["value"] == 0
    assert agent["human_intervention_rate"]["value"] == 0.0
    assert agent["median_elapsed_seconds"]["value"] > 0


def test_failed_first_pass_and_repair_are_visible():
    value = attempt(
        preflight=("FAIL", "PASS"),
        ci=("PASS",),
        repairs=("TEST_FAILURE",),
    )
    result = report(attempt_to_events(value, suite()))
    agent = first_agent(result)
    assert agent["first_pass_preflight_rate"]["value"] == 0.0
    assert agent["first_pass_ci_rate"]["value"] == 1.0
    assert agent["median_repair_iterations"]["value"] == 1
    assert agent["repeated_failure_classes"] == {}


def test_repeated_failure_classes_require_recurrence():
    events = []
    for run_id in ("eval-1", "eval-2"):
        value = attempt(
            evaluation_run_id=run_id,
            repairs=("TEST_FAILURE",),
        )
        events.extend(attempt_to_events(value, suite()))
    result = report(events)
    assert first_agent(result)["repeated_failure_classes"] == {"TEST_FAILURE": 2}


def test_human_intervention_is_measured_separately():
    value = attempt(interventions=("credential needed",))
    result = report(attempt_to_events(value, suite()))
    assert first_agent(result)["human_intervention_rate"]["value"] == 1.0


def test_missing_ci_and_acceptance_remain_unknown_not_zero():
    value = attempt(ci=(), acceptance=None)
    events = attempt_to_events(value, suite())
    result = report(events)
    agent = first_agent(result)
    assert agent["first_pass_ci_rate"]["status"] == "UNKNOWN"
    assert agent["deterministic_task_pass_rate"]["status"] == "UNKNOWN"
    assert agent["median_elapsed_seconds"]["status"] == "UNKNOWN"


def test_measured_cost_requires_explicit_evidence():
    without = report(attempt_to_events(attempt(), suite()))
    assert first_agent(without)["measured_cost"]["status"] == "UNKNOWN"

    with_cost = attempt(
        measured_cost={
            "value": 1.25,
            "currency": "USD",
            "evidence_refs": ["billing:1"],
        }
    )
    measured = report(attempt_to_events(with_cost, suite()))
    cost = first_agent(measured)["measured_cost"]["USD"]
    assert cost["status"] == "MEASURED"
    assert cost["value"] == 1.25
    assert "billing:1" in cost["evidence_refs"]


def test_comparison_requires_same_baseline_and_task_coverage():
    events = []
    events.extend(
        attempt_to_events(
            attempt(evaluation_run_id="eval-a", agent_id="agent-a", config="a"),
            suite(),
        )
    )
    events.extend(
        attempt_to_events(
            attempt(evaluation_run_id="eval-b", agent_id="agent-b", config="b"),
            suite(),
        )
    )
    comparable = report(events)
    assert comparable["cohorts"][0]["comparison_status"] == "COMPARABLE"

    one_agent = report(
        attempt_to_events(
            attempt(evaluation_run_id="eval-only", agent_id="agent-a", config="a"),
            suite(),
        )
    )
    assert one_agent["cohorts"][0]["comparison_status"] == "INSUFFICIENT_DATA"
