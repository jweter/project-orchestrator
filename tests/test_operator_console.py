from project_orchestrator.operator_console import build_snapshot, render_html


def test_console_exposes_execution_learning_failures_and_debt():
    snapshot = build_snapshot(
        scheduler_report={
            "executive_status": "YELLOW",
            "management_bottom_line": "one lane waiting",
            "kpis": {"product_advancement_rate": {"value": 2 / 3}},
            "no_advancement_reasons": {"everward": "waiting on CI"},
            "results": {
                "BLOCKED": [],
                "WAITING_ON_PREFLIGHT": [],
                "WAITING_ON_CI": ["everward#292"],
            },
            "project_orchestrator": {"state": "GREEN"},
            "learning_evolution": {"reused": 1},
            "jeremy_can_help": [],
        },
        actor_events=[
            {
                "timestamp_utc": "2026-09-18T13:00:00+00:00",
                "state": "EXECUTING",
                "memory_percent": 70,
            }
        ],
        retained_learning={
            "schema_version": 1,
            "rules": [{"rule_id": "rule-1"}],
        },
        process_experiments={
            "schema_version": 1,
            "experiments": [{"state": "KEEP"}],
        },
        product_reality={"active_debt_count": 2},
    )
    assert snapshot["execution"]["actor"]["latest_state"] == "EXECUTING"
    assert snapshot["learning"]["retained_rule_count"] == 1
    assert snapshot["learning"]["process_experiment_states"]["KEEP"] == 1
    assert snapshot["verification_debt"]["waiting_on_ci"] == ["everward#292"]
    assert snapshot["product_reality"]["active_debt_count"] == 2


def test_console_html_is_self_contained_and_escaped():
    html = render_html({"execution": {"management_bottom_line": "<unsafe>"}})
    assert "Daedalus Operator Console" in html
    assert "&lt;unsafe&gt;" in html
    assert "<unsafe>" not in html
