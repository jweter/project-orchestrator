import pytest

from project_orchestrator.candidate_registry import attach_verified_learning
from project_orchestrator.learning import LearningMemoryError, validate_lesson


def fixtures():
    lesson=validate_lesson({
        "project":"knowledge-engine","repository":"jweter/knowledge-engine-ai",
        "source_type":"success_pattern","source_ref":"knowledge-engine-ai#162",
        "symptom":"research report evidence boundary gap",
        "root_cause":"parser omitted direct-evidence invariant",
        "fix":"add deterministic parser invariant",
        "verification":"exact-head verification passed",
        "regression_protection":"keep the evidence boundary explicit",
        "residual_risk":"final readability remains Product Reality",
        "evidence_state":"VERIFIED",
    })
    memory={"schema_version":1,"entries":[lesson]}
    catalog={"schema_version":1,"skills":[{
        "skill_key":"research-report-evidence-boundary-validator",
        "project":"knowledge-engine","repository":"jweter/knowledge-engine-ai",
        "status":"PILOT_READY","authority":"ADVISORY_ONLY",
        "source_lesson_ids":[lesson["lesson_id"]],"history_refs":["commit:"+("a"*40)],
        "path_patterns":["knowledge_engine_ai/copilot/research_report.py"],
        "task_triggers":["research report"],"problem":"evidence boundary",
        "prevention_rule":"pair guidance with deterministic validation",
        "verification_commands":["python engineering/preflight.py"],
        "residual_risk":"readability stays separate","effectiveness_state":"UNMEASURED",
        "product_reality_may_be_inferred":False,
    }]}
    return memory,catalog


def candidate(**kw):
    item={"project":"knowledge-engine","repository":"jweter/knowledge-engine-ai",
          "source_type":"decomposition","source_ref":"issue:200","source_revision":"1",
          "source_state":"OPEN","priority":"P5","dependencies":[],"conflict_free":True,
          "paths":["knowledge_engine_ai/copilot/research_report.py"],
          "task":"strengthen provenance"}
    item.update(kw)
    return item


def test_explicit_verified_capability_is_attached():
    memory,catalog=fixtures()
    got=attach_verified_learning(candidate(),memory,catalog)
    assert got["learning_gate"]=="VERIFIED_CAPABILITIES_SELECTED"
    ref=got["capability_reuse_refs"][0]
    assert ref["evidence_state"]=="VERIFIED"
    assert ref["matched_paths"]
    assert ref["reuse_outcome"]=="PENDING_VERIFIED_OUTCOME"
    assert ref["product_reality_may_be_inferred"] is False


def test_unrelated_work_gets_no_capability():
    memory,catalog=fixtures()
    got=attach_verified_learning(
        candidate(paths=["knowledge_engine_ai/providers/search.py"],task="provider retry"),
        memory,catalog,
    )
    assert got["learning_gate"]=="NO_EXPLICIT_CAPABILITY_MATCH"
    assert got["capabilities_to_reuse"]==[]


def test_unverified_learning_cannot_back_index():
    memory,catalog=fixtures()
    memory["entries"][0]["evidence_state"]="PENDING"
    with pytest.raises(LearningMemoryError,match="only VERIFIED"):
        attach_verified_learning(candidate(),memory,catalog)
