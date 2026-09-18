from project_orchestrator.retained_learning import add_rule, empty_store, retain_from_experiment
from project_orchestrator.scheduler_policy import apply_retained_policy, scheduler_context


def store():
    exp = {
        "experiment_id": "pex-7",
        "repository": "jweter/knowledge-engine-core",
        "state": "KEEP",
        "decision_evidence": ["ci:green", "post:measured"],
        "safety_invariants": ["never weaken CI"],
    }
    rule = retain_from_experiment(
        exp,
        mechanism="provision locked environment before canonical preflight",
        applicability={"path_prefixes": ["knowledge_engine/"], "failure_classes": ["ENVIRONMENT_FAILURE"]},
    )
    return add_rule(empty_store(), rule), rule


def test_scheduler_receives_applicable_verified_rule():
    s, rule = store()
    item = {"repository": "jweter/knowledge-engine-core", "changed_paths": ["knowledge_engine/a.py"], "failure_class": "ENVIRONMENT_FAILURE"}
    result = apply_retained_policy(s, item)
    assert result["retained_policy"]["applied_rule_ids"] == [rule["rule_id"]]
    assert result["retained_policy"]["mechanisms"] == ["provision locked environment before canonical preflight"]


def test_rule_does_not_cross_repository_boundary():
    s, _ = store()
    item = {"repository": "jweter/Project-Everward", "changed_paths": ["knowledge_engine/a.py"], "failure_class": "ENVIRONMENT_FAILURE"}
    assert apply_retained_policy(s, item)["retained_policy"]["applied_rule_ids"] == []


def test_scheduler_context_preserves_work_items():
    s, _ = store()
    items = [{"repository": "jweter/knowledge-engine-core", "changed_paths": []}]
    result = scheduler_context(s, items)
    assert result[0]["repository"] == items[0]["repository"]
    assert result[0]["retained_policy"]["authority"] == "ADVISORY_ONLY_NORMAL_GATES_REQUIRED"
