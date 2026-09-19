from project_orchestrator.context_compression_benchmark import EvidenceCase, benchmark_case, benchmark_suite, promotion_decision

CASE=EvidenceCase("ci","INFO repeat\nINFO repeat\nERROR first meaningful failure: test_x\nSHA abc123",("ERROR first meaningful failure: test_x","SHA abc123"))

def test_identity_preserves_signal_but_does_not_earn_pilot():
    r=benchmark_suite([CASE],lambda x:x)
    assert r["signal_retention"]==1.0
    assert promotion_decision(r)=="BENCHMARK_ONLY"

def test_compressor_can_earn_pilot_only_with_full_signal():
    r=benchmark_suite([CASE],lambda _: "ERROR first meaningful failure: test_x\nSHA abc123")
    assert r["compression_ratio"] < 0.7
    assert promotion_decision(r)=="PILOT_READY"

def test_signal_loss_fails_closed_even_with_high_compression():
    r=benchmark_suite([CASE],lambda _: "ERROR")
    assert promotion_decision(r)=="REJECT_SIGNAL_LOSS"

def test_non_text_compressor_rejected():
    import pytest
    with pytest.raises(TypeError): benchmark_case(CASE,lambda _:None)

def test_report_labels_token_counts_as_estimates():
    r=benchmark_suite([CASE],lambda x:x)
    assert "ESTIMATE" in r["token_metric"]
