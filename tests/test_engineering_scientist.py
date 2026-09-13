from __future__ import annotations

import pytest

from project_orchestrator.engineering_scientist import (
    EngineeringScientistError,
    build_experiment_record,
    compare_hypotheses,
    distill_cross_repo_patterns,
    schedule_portfolio_resources,
    sweep_opportunities,
    validate_scientist_cycle,
)


SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40


def contract(**overrides):
    value = {
        "contract_id": "latency-v1",
        "primary_metric": "latency_ms",
        "direction": "MINIMIZE",
        "tolerance": 1.0,
        "required_metrics": ["latency_ms", "failures"],
    }
    value.update(overrides)
    return value


def resource(**overrides):
    value = {
        "compute_seconds_estimate": 60,
        "network_calls_estimate": 2,
        "write_operations_estimate": 1,
        "concurrency_estimate": 1,
        "external_cost_usd_estimate": 0,
        "exclusive_resources": [],
    }
    value.update(overrides)
    return value


def experiment(
    *,
    hypothesis_id="h1",
    candidate_head_sha=SHA_B,
    latency=100.0,
    state="COMPLETE",
    verification_state="VERIFIED_PASS",
    measurement_contract=None,
    safety_violations=None,
    attempt=1,
    parent_experiment_id=None,
):
    return build_experiment_record(
        project="knowledge-engine",
        repository="jweter/knowledge-engine-core",
        baseline_head_sha=SHA_A,
        candidate_head_sha=candidate_head_sha,
        hypothesis_id=hypothesis_id,
        hypothesis=f"Hypothesis {hypothesis_id}",
        baseline_ref="preflight:baseline",
        measurement_contract=measurement_contract or contract(),
        scope_refs=["src/example.py"],
        evidence_refs=["issue:#59"],
        resource_estimate=resource(),
        action_guard_state="READY",
        attempt=attempt,
        parent_experiment_id=parent_experiment_id,
        state=state,
        verification_state=verification_state,
        result_metrics={"latency_ms": latency, "failures": 0} if state == "COMPLETE" else None,
        verification_evidence_refs=["ci:green"] if verification_state != "UNVERIFIED" else [],
        safety_violations=safety_violations or [],
    )


def test_experiment_record_has_durable_lineage_and_exact_heads():
    first = experiment(state="PLANNED", verification_state="UNVERIFIED")
    second = build_experiment_record(
        project="knowledge-engine",
        repository="jweter/knowledge-engine-core",
        baseline_head_sha=SHA_A,
        candidate_head_sha=SHA_C,
        hypothesis_id="h1b",
        hypothesis="Retry with bounded correction",
        baseline_ref="preflight:baseline",
        measurement_contract=contract(),
        scope_refs=["src/example.py"],
        evidence_refs=["issue:#59", "failure:exact"],
        resource_estimate=resource(),
        action_guard_state="READY",
        attempt=2,
        parent_experiment_id=first["experiment_id"],
        parent_hypothesis_id=first["hypothesis_id"],
    )
    assert second["parent_experiment_id"] == first["experiment_id"]
    assert second["parent_hypothesis_id"] == "h1"
    assert second["baseline_head_sha"] == SHA_A
    assert second["candidate_head_sha"] == SHA_C


def test_retry_requires_parent_experiment():
    with pytest.raises(EngineeringScientistError, match="parent_experiment_id"):
        build_experiment_record(
            project="knowledge-engine",
            repository="jweter/knowledge-engine-core",
            baseline_head_sha=SHA_A,
            candidate_head_sha=SHA_B,
            hypothesis_id="retry",
            hypothesis="Retry",
            baseline_ref="preflight:baseline",
            measurement_contract=contract(),
            scope_refs=["src/example.py"],
            evidence_refs=["failure:exact"],
            resource_estimate=resource(),
            action_guard_state="READY",
            attempt=2,
        )


def test_completed_experiment_requires_ready_action_guard():
    with pytest.raises(EngineeringScientistError, match="action_guard_state=READY"):
        build_experiment_record(
            project="knowledge-engine",
            repository="jweter/knowledge-engine-core",
            baseline_head_sha=SHA_A,
            candidate_head_sha=SHA_B,
            hypothesis_id="h1",
            hypothesis="test",
            baseline_ref="preflight:baseline",
            measurement_contract=contract(),
            scope_refs=["src/example.py"],
            evidence_refs=["issue:#59"],
            resource_estimate=resource(),
            action_guard_state="GATHER_CONTEXT",
            state="COMPLETE",
            verification_state="VERIFIED_PASS",
            result_metrics={"latency_ms": 10, "failures": 0},
            verification_evidence_refs=["ci:green"],
        )


def test_hypothesis_competition_promotes_clear_verified_winner():
    slow = experiment(hypothesis_id="slow", candidate_head_sha=SHA_B, latency=105)
    fast = experiment(hypothesis_id="fast", candidate_head_sha=SHA_C, latency=90)
    result = compare_hypotheses([slow, fast])
    assert result["state"] == "PROMOTE_CANDIDATE"
    assert result["winner_experiment_id"] == fast["experiment_id"]
    assert result["observed_margin"] == 15


def test_hypothesis_competition_refuses_contract_mismatch():
    first = experiment(hypothesis_id="h1", latency=100)
    second = experiment(
        hypothesis_id="h2",
        candidate_head_sha=SHA_C,
        latency=90,
        measurement_contract=contract(contract_id="different-v2"),
    )
    result = compare_hypotheses([first, second])
    assert result["state"] == "INCOMPARABLE"
    assert "measurement_contract_mismatch" in result["reasons"]


def test_hypothesis_competition_requires_two_verified_candidates():
    good = experiment(hypothesis_id="good", latency=100)
    red = experiment(
        hypothesis_id="red",
        candidate_head_sha=SHA_C,
        latency=80,
        verification_state="VERIFIED_FAIL",
    )
    result = compare_hypotheses([good, red])
    assert result["state"] == "INCONCLUSIVE"
    assert result["winner_experiment_id"] is None


def test_hypothesis_competition_does_not_promote_within_tolerance():
    first = experiment(hypothesis_id="h1", latency=100)
    second = experiment(hypothesis_id="h2", candidate_head_sha=SHA_C, latency=100.5)
    result = compare_hypotheses([first, second])
    assert result["state"] == "NO_CLEAR_WINNER"
    assert result["winner_experiment_id"] is None


def test_safety_violation_disqualifies_candidate_even_if_metric_is_best():
    safe = experiment(hypothesis_id="safe", latency=100)
    unsafe = experiment(
        hypothesis_id="unsafe",
        candidate_head_sha=SHA_C,
        latency=50,
        safety_violations=["provenance boundary violated"],
    )
    result = compare_hypotheses([safe, unsafe])
    assert result["state"] == "INCONCLUSIVE"
    assert result["rejected"][0]["reason"] == "safety_violation_recorded"


def action(action_id, project, priority, **resource_overrides):
    return {
        "action_id": action_id,
        "project": project,
        "repository": f"jweter/{project}",
        "priority": priority,
        "action_guard_state": "READY",
        "resource_estimate": resource(**resource_overrides),
        "evidence_refs": ["issue:#59"],
    }


def capacity(**overrides):
    value = {
        "compute_seconds": 1000,
        "network_calls": 100,
        "write_operations": 20,
        "concurrency": 2,
        "external_cost_usd": 0,
        "exclusive_resource_slots": {"ollama": 1, "unreal": 1},
    }
    value.update(overrides)
    return value


def test_resource_scheduler_is_fair_within_same_priority():
    result = schedule_portfolio_resources(
        actions=[
            action("ke-1", "knowledge-engine", 5),
            action("ke-2", "knowledge-engine", 5),
            action("rock-1", "rocksmith", 5),
        ],
        capacity=capacity(concurrency=2),
    )
    assert [row["action_id"] for row in result["scheduled"]] == ["ke-1", "rock-1"]
    assert result["deferred"][0]["action_id"] == "ke-2"


def test_resource_scheduler_preserves_priority_over_fairness():
    result = schedule_portfolio_resources(
        actions=[
            action("p0-a", "knowledge-engine", 0, concurrency_estimate=2),
            action("p5-b", "rocksmith", 5),
        ],
        capacity=capacity(concurrency=2),
    )
    assert [row["action_id"] for row in result["scheduled"]] == ["p0-a"]
    assert result["deferred"][0]["action_id"] == "p5-b"


def test_resource_scheduler_blocks_non_ready_action():
    item = action("blocked", "everward", 3)
    item["action_guard_state"] = "APPROVAL_REQUIRED"
    result = schedule_portfolio_resources(actions=[item], capacity=capacity())
    assert result["state"] == "NO_ACTION_SCHEDULED"
    assert result["deferred"][0]["state"] == "BLOCKED_BY_ACTION_GUARD"


def test_resource_scheduler_respects_exclusive_resource_slots():
    result = schedule_portfolio_resources(
        actions=[
            action("one", "knowledge-engine", 5, exclusive_resources=["ollama"]),
            action("two", "rocksmith", 5, exclusive_resources=["ollama"]),
        ],
        capacity=capacity(concurrency=4),
    )
    assert len(result["scheduled"]) == 1
    assert any("exclusive_resource_unavailable:ollama" in row["reason"] for row in result["deferred"])


def opportunity(opportunity_id, **overrides):
    value = {
        "opportunity_id": opportunity_id,
        "project": "knowledge-engine",
        "repository": "jweter/knowledge-engine-core",
        "source_type": "recurring_failure",
        "priority": 5,
        "scope_refs": [f"src/{opportunity_id}.py"],
        "evidence_refs": [f"evidence:{opportunity_id}"],
        "hypothesis": f"bounded change for {opportunity_id} improves the measured contract",
        "measurement_contract": contract(contract_id=f"{opportunity_id}-contract"),
        "reversible": True,
        "bounded_scope": True,
        "deterministic_measurement": True,
        "active_coverage_refs": [],
        "measured_signals": [{"name": "repeat_failures", "value": 2, "evidence_ref": f"metric:{opportunity_id}"}],
    }
    value.update(overrides)
    return value


def test_opportunity_sweeper_proposes_only_evidence_backed_bounded_experiments():
    result = sweep_opportunities(
        opportunities=[
            opportunity("good"),
            opportunity("no-evidence", evidence_refs=[], measured_signals=[]),
            opportunity("not-reversible", reversible=False),
        ],
        max_proposals=5,
    )
    states = {row["opportunity_id"]: row["state"] for row in result["opportunities"]}
    assert states["good"] == "PROPOSE_EXPERIMENT"
    assert states["no-evidence"] == "GATHER_EVIDENCE"
    assert states["not-reversible"] == "NOT_AUTONOMOUSLY_EXPERIMENTABLE"


def test_opportunity_sweeper_deduplicates_active_or_equivalent_work():
    first = opportunity("first", scope_refs=["src/shared.py"], measurement_contract=contract(contract_id="shared"))
    second = opportunity("second", scope_refs=["src/shared.py"], measurement_contract=contract(contract_id="shared"))
    active = opportunity("active", active_coverage_refs=["pr:#100"])
    result = sweep_opportunities(opportunities=[first, second, active], max_proposals=5)
    states = {row["opportunity_id"]: row["state"] for row in result["opportunities"]}
    assert {states["first"], states["second"]} == {"PROPOSE_EXPERIMENT", "DUPLICATE"}
    assert states["active"] == "ALREADY_COVERED"


def lesson(lesson_id, repository, project, **overrides):
    value = {
        "lesson_id": lesson_id,
        "repository": repository,
        "project": project,
        "pattern_key": "exact-head-independent-verification",
        "mechanism": "verify exact candidate head independently before promotion",
        "applicability_conditions": ["candidate change has a deterministic verification path"],
        "counterexamples": ["subjective Product Reality acceptance"],
        "evidence_refs": [f"evidence:{lesson_id}"],
        "verified_outcome": "EARLIER_DETECTION",
        "verified_outcome_ref": f"outcome:{lesson_id}",
        "domain_independent": True,
        "product_requirement_transfer": False,
    }
    value.update(overrides)
    return value


def test_pattern_distillation_requires_independent_repo_and_project_evidence():
    result = distill_cross_repo_patterns(
        lessons=[
            lesson("ke", "jweter/knowledge-engine-core", "knowledge-engine"),
            lesson("rock", "jweter/rocksmith-cdlc-generator", "rocksmith"),
        ]
    )
    pattern = result["patterns"][0]
    assert pattern["state"] == "PORTFOLIO_PATTERN_CANDIDATE"
    assert pattern["promotion_authority"] == "CANDIDATE_ONLY"


def test_pattern_distillation_keeps_single_project_pattern_local():
    result = distill_cross_repo_patterns(
        lessons=[
            lesson("core", "jweter/knowledge-engine-core", "knowledge-engine"),
            lesson("web", "jweter/knowledge-engine-web", "knowledge-engine"),
        ]
    )
    assert result["patterns"][0]["state"] == "LOCAL_OR_PROJECT_ONLY"


def test_pattern_distillation_blocks_domain_requirement_transfer():
    result = distill_cross_repo_patterns(
        lessons=[
            lesson(
                "rock",
                "jweter/rocksmith-cdlc-generator",
                "rocksmith",
                domain_independent=False,
                product_requirement_transfer=True,
            )
        ]
    )
    assert result["patterns"][0]["state"] == "REJECT_DOMAIN_COUPLED"


def stages(state="COMPLETE"):
    value = {}
    for stage in (
        "OBSERVE",
        "FORMULATE_HYPOTHESIS",
        "RETRIEVE_PRIOR_EVIDENCE",
        "DESIGN_BOUNDED_EXPERIMENT",
        "ESTIMATE_COST",
        "ACTION_GUARD",
        "EXECUTE",
        "MEASURE",
        "COMPARE",
        "INDEPENDENT_VERIFY",
        "RECORD",
        "GENERALIZE",
        "REPEAT",
    ):
        value[stage] = {"state": state, "evidence_ref": f"evidence:{stage.lower()}"}
    return value


def test_scientist_cycle_closes_only_with_governed_execution_and_evidence():
    result = validate_scientist_cycle(
        {
            "schema_version": 1,
            "cycle_id": "cycle-1",
            "project": "knowledge-engine",
            "repository": "jweter/knowledge-engine-core",
            "baseline_head_sha": SHA_A,
            "action_guard_state": "READY",
            "schedule_state": "SCHEDULED",
            "measurement_contract_ref": "contract:latency-v1",
            "stages": stages(),
        }
    )
    assert result["cycle_closed"] is True


def test_scientist_cycle_cannot_execute_without_ready_guard_and_schedule():
    with pytest.raises(EngineeringScientistError, match="action_guard_state=READY"):
        validate_scientist_cycle(
            {
                "schema_version": 1,
                "cycle_id": "cycle-1",
                "project": "knowledge-engine",
                "repository": "jweter/knowledge-engine-core",
                "baseline_head_sha": SHA_A,
                "action_guard_state": "GATHER_CONTEXT",
                "schedule_state": "SCHEDULED",
                "measurement_contract_ref": "contract:latency-v1",
                "stages": stages(),
            }
        )


def test_scientist_cycle_cannot_generalize_before_independent_verification():
    s = stages()
    s["INDEPENDENT_VERIFY"] = {"state": "BLOCKED", "reason": "CI pending"}
    with pytest.raises(EngineeringScientistError, match="INDEPENDENT_VERIFY"):
        validate_scientist_cycle(
            {
                "schema_version": 1,
                "cycle_id": "cycle-1",
                "project": "knowledge-engine",
                "repository": "jweter/knowledge-engine-core",
                "baseline_head_sha": SHA_A,
                "action_guard_state": "READY",
                "schedule_state": "SCHEDULED",
                "measurement_contract_ref": "contract:latency-v1",
                "stages": s,
            }
        )
