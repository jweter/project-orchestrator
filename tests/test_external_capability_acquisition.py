from project_orchestrator.external_capability_acquisition import (
    Disposition, ExternalCapability, classify, evaluation_record, rank,
)

def candidate(**overrides):
    values = dict(name="OpenSpec", repository="Fission-AI/OpenSpec", license="MIT",
        capabilities=("specs", "cross_repo_planning"), target_products=("orchestrator",),
        priority=5, dependency_risk=1, evidence_url="https://github.com/Fission-AI/OpenSpec")
    values.update(overrides)
    return ExternalCapability(**values)

def test_overlap_prefers_adaptation_over_duplicate_dependency():
    assert classify(candidate(), {"specs"}) is Disposition.ADAPT

def test_low_risk_novel_capability_can_integrate():
    assert classify(candidate(), set()) is Disposition.INTEGRATE

def test_non_permissive_license_is_benchmark_only():
    assert classify(candidate(license="AGPL-3.0"), set()) is Disposition.BENCHMARK

def test_empty_candidate_is_rejected():
    assert classify(candidate(capabilities=()), set()) is Disposition.REJECT

def test_rank_is_deterministic_and_value_weighted():
    low = candidate(name="Low", priority=1)
    high = candidate(name="High", priority=5)
    assert [x.name for x in rank([low, high])] == ["High", "Low"]

def test_record_is_evidence_gated_not_auto_adopted():
    record = evaluation_record(candidate(), {"specs"})
    assert record["status"] == "EVALUATION_REQUIRED"
    assert record["disposition"] == "ADAPT"
    assert record["evidence_url"]
