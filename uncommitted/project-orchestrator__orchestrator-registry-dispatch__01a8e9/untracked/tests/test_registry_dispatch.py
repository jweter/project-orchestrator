from project_orchestrator.candidate_registry import refresh_registry
from project_orchestrator.learning import validate_lesson
from project_orchestrator.registry_dispatch import process_registry_event


POLICY = {
    "name": "Exponential Engine v1",
    "capacity_controller": {
        "initial_max_concurrent_product_lanes": 3,
        "min_concurrent_product_lanes": 1,
        "max_concurrent_product_lanes": 6,
        "increase_by": 1,
        "decrease_by": 1,
        "minimum_evidence_windows_before_change": 3,
    },
}


def candidate(**overrides):
    item = {
        "project": "knowledge-engine",
        "repository": "jweter/knowledge-engine-ai",
        "source_type": "decomposition",
        "source_ref": "issue:200/report",
        "source_revision": "1",
        "source_state": "OPEN",
        "priority": "P5",
        "paths": ["knowledge_engine_ai/copilot/research_report.py"],
        "dependencies": [],
        "task": "strengthen research report provenance",
        "conflict_free": True,
    }
    item.update(overrides)
    return item
def learning_context():
    lesson = validate_lesson(
        {
            "project": "knowledge-engine",
            "repository": "jweter/knowledge-engine-ai",
            "source_type": "success_pattern",
            "source_ref": "knowledge-engine-ai#162",
            "symptom": "research report evidence boundary gap",
            "root_cause": "parser omitted direct-evidence invariant",
            "fix": "add deterministic parser invariant",
            "verification": "exact-head verification passed",
            "regression_protection": "keep the evidence boundary explicit",
            "residual_risk": "readability remains Product Reality",
            "evidence_state": "VERIFIED",
        }
    )
    memory = {"schema_version": 1, "entries": [lesson]}
    catalog = {
        "schema_version": 1,
        "skills": [
            {
                "skill_key": "research-report-evidence-boundary-validator",
                "project": "knowledge-engine",
                "repository": "jweter/knowledge-engine-ai",
                "status": "PILOT_READY",
                "authority": "ADVISORY_ONLY",
                "source_lesson_ids": [lesson["lesson_id"]],
                "history_refs": ["commit:" + ("a" * 40)],
                "path_patterns": [
                    "knowledge_engine_ai/copilot/research_report.py"
                ],
                "task_triggers": ["research report"],
                "problem": "report output can cross an evidence boundary",
                "prevention_rule": "pair guidance with deterministic validation",
                "verification_commands": ["python engineering/preflight.py"],
                "residual_risk": "human readability remains separate",
                "effectiveness_state": "UNMEASURED",
                "product_reality_may_be_inferred": False,
            }
        ],
    }
    return memory, catalog


def dispatch(registry):
    memory, catalog = learning_context()
    return process_registry_event(
        registry,
        memory=memory,
        skill_catalog=catalog,
        policy=POLICY,
        event={"type": "hourly_scheduler_run"},
    )


def test_registry_candidate_flows_to_worker_with_verified_capability():
    registry = refresh_registry(
        {"schema_version": 1, "entries": []}, [candidate()]
    )
    outcome = dispatch(registry)
    assert outcome["action"] == "DISPATCH"
    assert len(outcome["worker_envelopes"]) == 1
    envelope = outcome["worker_envelopes"][0]
    assert len(envelope["capabilities_to_reuse"]) == 1
    assert envelope["merge_authority"] is False
    assert envelope["product_reality_authority"] is False
    bridge = outcome["registry_bridge"]
    assert bridge["selected_capability_reuse_count"] == 1
    assert bridge["product_reality_inferred"] is False


def test_unrelated_capability_is_omitted_but_safe_work_still_dispatches():
    registry = refresh_registry(
        {"schema_version": 1, "entries": []},
        [candidate(paths=["knowledge_engine_ai/providers/search.py"], task="provider retry")],
    )
    outcome = dispatch(registry)
    assert outcome["action"] == "DISPATCH"
    assert outcome["worker_envelopes"][0]["capabilities_to_reuse"] == []
    assert outcome["registry_bridge"]["attached_capability_count"] == 0


def test_terminal_or_owned_registry_entry_cannot_dispatch():
    for blocked in (
        candidate(source_state="CLOSED"),
        candidate(actively_owned=True),
        candidate(product_reality_required=True),
    ):
        registry = refresh_registry(
            {"schema_version": 1, "entries": []}, [blocked]
        )
        outcome = dispatch(registry)
        assert outcome["action"] == "IDLE"
        assert outcome["worker_envelopes"] == []


def test_repeated_registry_event_is_deterministic():
    registry = refresh_registry(
        {"schema_version": 1, "entries": []}, [candidate()]
    )
    assert dispatch(registry) == dispatch(registry)
