import pytest

from project_orchestrator.retained_learning import add_rule, applicable_rules, empty_store, retain_from_experiment


def kept():
    return {
        "experiment_id": "pex-1",
        "repository": "jweter/project-orchestrator",
        "state": "KEEP",
        "decision_evidence": ["baseline-1", "ci-1", "post-1"],
        "safety_invariants": ["never weaken CI"],
    }


def test_only_keep_can_be_retained():
    exp = kept()
    exp["state"] = "REJECT"
    with pytest.raises(ValueError, match="only KEEP"):
        retain_from_experiment(exp, mechanism="reuse", applicability={"path_prefixes": ["scripts/"]})


def test_retained_rule_requires_measured_decision_evidence():
    exp = kept()
    exp["decision_evidence"] = []
    with pytest.raises(ValueError, match="decision evidence"):
        retain_from_experiment(exp, mechanism="reuse", applicability={"path_prefixes": ["scripts/"]})


def test_repository_rule_matches_only_applicable_work():
    rule = retain_from_experiment(
        kept(),
        mechanism="provision verification environment before preflight",
        applicability={"path_prefixes": ["scripts/"], "failure_classes": ["ENVIRONMENT_FAILURE"]},
    )
    store = add_rule(empty_store(), rule)
    assert applicable_rules(store, repository="jweter/project-orchestrator", changed_paths=["scripts/a.py"], failure_class="ENVIRONMENT_FAILURE") == [rule]
    assert applicable_rules(store, repository="jweter/other", changed_paths=["scripts/a.py"], failure_class="ENVIRONMENT_FAILURE") == []
    assert applicable_rules(store, repository="jweter/project-orchestrator", changed_paths=["src/a.py"], failure_class="ENVIRONMENT_FAILURE") == []


def test_duplicate_rule_is_idempotent():
    rule = retain_from_experiment(kept(), mechanism="reuse", applicability={"path_prefixes": ["scripts/"]})
    store = add_rule(empty_store(), rule)
    assert add_rule(store, rule) == store


def test_cli_retain_path_can_feed_scheduler_context(tmp_path, monkeypatch):
    import json
    from project_orchestrator import cli
    exp = kept()
    experiments = {"schema_version": 1, "experiments": [exp]}
    exp_path = tmp_path / "experiments.json"
    retained_path = tmp_path / "retained.json"
    exp_path.write_text(json.dumps(experiments), encoding="utf-8")
    retained_path.write_text(json.dumps(empty_store()), encoding="utf-8")
    applicability = tmp_path / "applicability.json"
    applicability.write_text(json.dumps({"path_prefixes": ["scripts/"], "failure_classes": ["ENVIRONMENT_FAILURE"]}), encoding="utf-8")
    monkeypatch.setattr(cli, "DEFAULT_PROCESS_EXPERIMENTS", exp_path)
    monkeypatch.setattr(cli, "DEFAULT_RETAINED_LEARNING", retained_path)
    cli.process_experiment_retain(exp["experiment_id"], "provision before verify", applicability, "repository")
    stored = json.loads(retained_path.read_text(encoding="utf-8"))
    assert len(stored["rules"]) == 1
    result = cli.scheduler_context(stored, [{"repository": exp["repository"], "changed_paths": ["scripts/x.py"], "failure_class": "ENVIRONMENT_FAILURE"}])
    assert result[0]["retained_policy"]["mechanisms"] == ["provision before verify"]
