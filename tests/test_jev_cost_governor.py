from project_orchestrator.jev_cost_governor import admission, decision_fingerprint, record_free_result


def request():
    return {"state": {"project_id": "everward"}, "questions": {"next": {"type": "choice"}}}


def test_no_free_credit_means_no_provider_call():
    result = admission(request(), state={"seen": {}, "calls": 0}, free_credit_available=False)
    assert result["admitted"] is False
    assert result["reason"] == "FREE_CREDIT_UNAVAILABLE"
    assert result["paid_spend_authorized"] is False


def test_free_credit_admits_uncached_decision_without_paid_authority():
    result = admission(request(), state={"seen": {}, "calls": 0}, free_credit_available=True)
    assert result["admitted"] is True
    assert result["reason"] == "FREE_CREDIT_ONLY"
    assert result["paid_spend_authorized"] is False


def test_identical_decision_is_cached_even_when_free_credit_exists():
    fp = decision_fingerprint(request())
    state = {"seen": {fp: {"choice": "a"}}, "calls": 1}
    result = admission(request(), state=state, free_credit_available=True)
    assert result["admitted"] is False
    assert result["reason"] == "CACHED_IDENTICAL_DECISION"
    assert result["cached"] == {"choice": "a"}


def test_recorded_free_result_increments_call_count():
    fp = decision_fingerprint(request())
    updated = record_free_result({"seen": {}, "calls": 0}, fingerprint=fp, result={"choice": "a"})
    assert updated["calls"] == 1
    assert updated["seen"][fp] == {"choice": "a"}
