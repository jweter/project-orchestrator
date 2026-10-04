import pytest

from project_orchestrator.process_experiments import activate, add_experiment, build_experiment, empty_store, evaluate, load_store, replace_experiment, write_store


def proposal():
    return {
        "repository": "jweter/project-orchestrator",
        "friction_type": "orchestration_control_failure",
        "evidence_refs": ["run-1", "run-2"],
        "proposed_mechanism": "repair recurring control failure",
        "verification_plan": ["reproduce", "test", "preflight", "CI"],
        "safety_boundaries": ["never weaken CI", "preserve Product Reality"],
    }


def test_build_requires_baseline_and_recurring_evidence():
    exp = build_experiment(proposal(), target_metrics=["failure_to_repair_seconds"], baseline_refs=["window-1"])
    assert exp["state"] == "BASELINE_READY"
    p = proposal(); p["evidence_refs"] = ["run-1"]
    with pytest.raises(ValueError, match="recurring"):
        build_experiment(p, target_metrics=["x"], baseline_refs=["window-1"])


def test_activation_requires_governed_change_and_verification_refs():
    exp = build_experiment(proposal(), target_metrics=["x"], baseline_refs=["baseline"])
    with pytest.raises(ValueError, match="verification_refs"):
        activate(exp, change_ref="pr-1", verification_refs=[])
    assert activate(exp, change_ref="pr-1", verification_refs=["preflight-green"])["state"] == "ACTIVE"


def test_keep_requires_measured_improvement_and_preserved_invariants():
    exp = activate(build_experiment(proposal(), target_metrics=["x"], baseline_refs=["baseline"]), change_ref="pr-1", verification_refs=["ci-green"])
    result = evaluate(exp, metric_results={"x": {"status": "MEASURED", "outcome": "IMPROVED"}}, post_change_refs=["window-2"], safety_invariants_passed=True)
    assert result["state"] == "KEEP"


def test_quality_regression_vetoes_improvement():
    exp = activate(build_experiment(proposal(), target_metrics=["x"], baseline_refs=["baseline"]), change_ref="pr-1", verification_refs=["ci-green"])
    result = evaluate(exp, metric_results={"x": {"status": "MEASURED", "outcome": "IMPROVED"}}, post_change_refs=["window-2"], safety_invariants_passed=False)
    assert result["state"] == "REJECT"


def test_unknown_and_missing_post_evidence_fail_closed():
    exp = activate(build_experiment(proposal(), target_metrics=["x"], baseline_refs=["baseline"]), change_ref="pr-1", verification_refs=["ci-green"])
    assert evaluate(exp, metric_results={"x": {"status": "UNKNOWN"}}, post_change_refs=["window-2"], safety_invariants_passed=True)["state"] == "INSUFFICIENT_DATA"
    assert evaluate(exp, metric_results={"x": {"status": "MEASURED", "outcome": "IMPROVED"}}, post_change_refs=[], safety_invariants_passed=True)["state"] == "INSUFFICIENT_DATA"


def test_merge_or_change_ref_alone_is_not_success():
    exp = activate(build_experiment(proposal(), target_metrics=["x"], baseline_refs=["baseline"]), change_ref="merged-pr-1", verification_refs=["ci-green"])
    result = evaluate(exp, metric_results={"x": {"status": "MEASURED", "outcome": "UNCHANGED"}}, post_change_refs=["window-2"], safety_invariants_passed=True)
    assert result["state"] == "REJECT"


def test_store_rejects_duplicate_experiment():
    exp = build_experiment(proposal(), target_metrics=["x"], baseline_refs=["baseline"])
    store = add_experiment(empty_store(), exp)
    with pytest.raises(ValueError, match="duplicate"):
        add_experiment(store, exp)



def test_store_round_trip_and_replace(tmp_path):
    path = tmp_path / "experiments.json"
    assert load_store(path) == empty_store()
    exp = build_experiment(proposal(), target_metrics=["x"], baseline_refs=["baseline"])
    store = add_experiment(empty_store(), exp)
    write_store(path, store)
    loaded = load_store(path)
    active = activate(exp, change_ref="pr-1", verification_refs=["ci-green"])
    replaced = replace_experiment(loaded, active)
    assert replaced["experiments"][0]["state"] == "ACTIVE"


def test_replace_rejects_unknown_experiment():
    exp = build_experiment(proposal(), target_metrics=["x"], baseline_refs=["baseline"])
    with pytest.raises(ValueError, match="unknown experiment_id"):
        replace_experiment(empty_store(), exp)


def test_build_carries_explicit_retention_contract():
    p = proposal()
    p["retention"] = {
        "mechanism": "reuse measured repair ordering",
        "applicability": {
            "path_prefixes": ["src/"],
            "failure_classes": ["QUALITY_FAILURE"],
        },
        "scope": "repository",
    }
    exp = build_experiment(p, target_metrics=["x"], baseline_refs=["baseline"])
    assert exp["retention"] == p["retention"]


def test_invalid_retention_contract_fails_closed():
    p = proposal()
    p["retention"] = {
        "mechanism": "reuse",
        "applicability": {},
        "scope": "repository",
    }
    with pytest.raises(ValueError, match="retention.applicability"):
        build_experiment(p, target_metrics=["x"], baseline_refs=["baseline"])
