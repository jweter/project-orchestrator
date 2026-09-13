from __future__ import annotations

import json

from project_orchestrator.evolution_cli import main
from project_orchestrator.evolution_engine import (
    build_evolution_plan,
    evaluate_material_review_gate,
)


SHA = "a" * 40


def _memory(entries=None):
    return {"schema_version": 1, "entries": entries or []}


def _event(**overrides):
    base = {
        "project": "everward",
        "repository": "jweter/Project-Everward",
        "pattern_id": "material-review-merge-gate",
        "evidence_ref": "everward#251-review-1",
        "evidence_state": "VERIFIED",
        "event_type": "FAILURE",
        "root_cause_verified": True,
        "repair_verified": True,
        "mechanism_brittle": False,
        "prevention_verified": False,
        "redesign_verified": False,
        "reserved_human_decision": False,
        "safety_regression": False,
        "learning_effect": "UNKNOWN",
    }
    base.update(overrides)
    return base


def _measurement(**overrides):
    base = {
        "repository": "jweter/Project-Everward",
        "pattern_id": "material-review-merge-gate",
        "evidence_ref": "metric-1",
        "evidence_state": "VERIFIED",
        "metric": "post_merge_correctness_hotfix_rate",
        "direction": "lower_is_better",
        "before": 0.5,
        "after": 0.0,
        "sample_count_before": 4,
        "sample_count_after": 4,
    }
    base.update(overrides)
    return base


def test_single_failure_starts_with_repair_and_does_not_jump_to_evolution() -> None:
    plan = build_evolution_plan(
        project="everward",
        repository="jweter/Project-Everward",
        pattern_id="material-review-merge-gate",
        memory=_memory(),
        events=[_event()],
    )
    assert plan["current_stage"] == "REPAIR"
    assert plan["recommended_next_stage"] == "UPGRADE"
    assert plan["evolution_eligible"] is False


def test_repeated_brittle_mechanism_escalates_to_replacement() -> None:
    events = [
        _event(evidence_ref="a", repair_verified=True, mechanism_brittle=True),
        _event(evidence_ref="b", repair_verified=True, mechanism_brittle=True),
    ]
    plan = build_evolution_plan(
        project="everward",
        repository="jweter/Project-Everward",
        pattern_id="material-review-merge-gate",
        memory=_memory(),
        events=events,
    )
    assert plan["current_stage"] == "REPLACEMENT"
    assert plan["recommended_next_stage"] == "UPGRADE"


def test_verified_prevention_moves_to_upgrade_but_metrics_are_required_for_evolution() -> None:
    plan = build_evolution_plan(
        project="everward",
        repository="jweter/Project-Everward",
        pattern_id="material-review-merge-gate",
        memory=_memory(),
        events=[_event(prevention_verified=True)],
    )
    assert plan["current_stage"] == "UPGRADE"
    assert plan["gate_status"] == "MEASURE_BEFORE_PROMOTION"
    assert plan["evolution_eligible"] is False


def test_measured_improvement_without_degradation_can_promote_to_evolution() -> None:
    plan = build_evolution_plan(
        project="everward",
        repository="jweter/Project-Everward",
        pattern_id="material-review-merge-gate",
        memory=_memory(),
        events=[_event(prevention_verified=True)],
        measurements=[_measurement()],
    )
    assert plan["current_stage"] == "EVOLUTION"
    assert plan["evolution_eligible"] is True
    assert plan["gate_status"] == "EVOLUTION_VERIFIED"


def test_any_measured_degradation_blocks_evolution() -> None:
    plan = build_evolution_plan(
        project="everward",
        repository="jweter/Project-Everward",
        pattern_id="material-review-merge-gate",
        memory=_memory(),
        events=[_event(prevention_verified=True)],
        measurements=[
            _measurement(),
            _measurement(
                metric="mean_cycle_time",
                direction="lower_is_better",
                before=10,
                after=12,
                evidence_ref="metric-2",
            ),
        ],
    )
    assert plan["current_stage"] == "UPGRADE"
    assert plan["evolution_eligible"] is False


def test_reserved_human_boundary_blocks_autonomous_promotion() -> None:
    plan = build_evolution_plan(
        project="everward",
        repository="jweter/Project-Everward",
        pattern_id="material-review-merge-gate",
        memory=_memory(),
        events=[_event(prevention_verified=True, reserved_human_decision=True)],
        measurements=[_measurement()],
    )
    assert plan["human_gate_required"] is True
    assert plan["evolution_eligible"] is False
    assert plan["gate_status"] == "HUMAN_DECISION_REQUIRED"


def test_unverified_evidence_never_drives_promotion() -> None:
    plan = build_evolution_plan(
        project="everward",
        repository="jweter/Project-Everward",
        pattern_id="material-review-merge-gate",
        memory=_memory(),
        events=[_event(evidence_state="PENDING")],
    )
    assert plan["gate_status"] == "HOLD_GATHER_EVIDENCE"
    assert plan["evolution_eligible"] is False


def test_learning_memory_does_not_leak_other_product_requirements() -> None:
    other = {
        "project": "rocksmith",
        "repository": "jweter/rocksmith-cdlc-generator",
        "source_type": "review_finding",
        "source_ref": "x",
        "symptom": "material review merge gate issue",
        "root_cause": "material review merge gate was ignored",
        "fix": "fix",
        "verification": "verified",
        "regression_protection": "material review merge gate test",
        "residual_risk": "none known",
        "evidence_state": "VERIFIED",
    }
    plan = build_evolution_plan(
        project="everward",
        repository="jweter/Project-Everward",
        pattern_id="material-review-merge-gate",
        memory=_memory([other]),
        events=[_event()],
    )
    assert plan["related_verified_lessons"] == []


def test_success_patterns_can_enter_upgrade_without_inventing_failure() -> None:
    first = _event(
        event_type="SUCCESS_PATTERN",
        evidence_ref="s1",
        root_cause_verified=False,
        repair_verified=False,
        prevention_verified=False,
    )
    second = _event(
        event_type="SUCCESS_PATTERN",
        evidence_ref="s2",
        root_cause_verified=False,
        repair_verified=False,
        prevention_verified=False,
    )
    plan = build_evolution_plan(
        project="everward",
        repository="jweter/Project-Everward",
        pattern_id="material-review-merge-gate",
        memory=_memory(),
        events=[first, second],
    )
    assert plan["current_stage"] == "UPGRADE"
    assert plan["failure_recurrence_count"] == 0


def test_commented_material_finding_blocks_merge() -> None:
    result = evaluate_material_review_gate(
        current_head_sha=SHA,
        reviewed_head_sha=SHA,
        review_state="COMMENTED",
        findings=[
            {
                "finding_id": "P2-1",
                "material": True,
                "resolved": False,
                "outdated": False,
            }
        ],
    )
    assert result["merge_allowed"] is False
    assert result["failure_class"] == "REVIEW_FAILURE"


def test_informational_comment_does_not_block_merge() -> None:
    result = evaluate_material_review_gate(
        current_head_sha=SHA,
        reviewed_head_sha=SHA,
        review_state="COMMENTED",
        findings=[
            {
                "finding_id": "info-1",
                "material": False,
                "resolved": False,
                "outdated": False,
            }
        ],
    )
    assert result["merge_allowed"] is True


def test_review_for_stale_head_requires_revalidation() -> None:
    result = evaluate_material_review_gate(
        current_head_sha=SHA,
        reviewed_head_sha="b" * 40,
        review_state="APPROVED",
        findings=[],
    )
    assert result["merge_allowed"] is False
    assert result["state"] == "REVIEW_REVALIDATION_REQUIRED"


def test_review_gate_cli_outputs_machine_readable_json(tmp_path, capsys) -> None:
    path = tmp_path / "review.json"
    path.write_text(
        json.dumps(
            {
                "current_head_sha": SHA,
                "reviewed_head_sha": SHA,
                "review_state": "COMMENTED",
                "findings": [
                    {
                        "finding_id": "P2-1",
                        "material": True,
                        "resolved": False,
                        "outdated": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert main(["review-gate", "--input", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["merge_allowed"] is False
    assert result["failure_class"] == "REVIEW_FAILURE"
