from project_orchestrator.jev_live_free import evaluate_free_live_shadow


CANDIDATES = [
    {"candidate_id":"a","project_id":"everward","repository":"jweter/Project-Everward","title":"A","eligibility_state":"ELIGIBLE"},
    {"candidate_id":"b","project_id":"everward","repository":"jweter/Project-Everward","title":"B","eligibility_state":"ELIGIBLE"},
]


def test_unknown_or_unavailable_free_credit_never_calls_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("JEV_SHADOW_LIVE", "1")
    calls = []
    result = evaluate_free_live_shadow(
        state={"project_id":"everward"}, candidates=CANDIDATES,
        governor_path=tmp_path/"governor.json", free_credit_available=False,
        transport=lambda *args, **kwargs: calls.append(1),
    )
    assert calls == []
    assert result["decision"]["state"] == "FREE_ONLY_BLOCKED"
    assert result["cost_governor"]["paid_spend_authorized"] is False


def test_free_credit_can_make_one_live_shadow_call_and_cache_it(tmp_path, monkeypatch):
    monkeypatch.setenv("JEV_SHADOW_LIVE", "1")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only-secret")
    calls = []
    def transport(payload, **kwargs):
        calls.append(payload)
        return {"answers":{"next_candidate":{"choice":"b","probabilities":{"a":0.2,"b":0.8}}}}
    path=tmp_path/"governor.json"
    first=evaluate_free_live_shadow(
        state={"project_id":"everward"}, candidates=CANDIDATES,
        governor_path=path, free_credit_available=True, transport=transport,
    )
    second=evaluate_free_live_shadow(
        state={"project_id":"everward"}, candidates=CANDIDATES,
        governor_path=path, free_credit_available=True, transport=transport,
    )
    assert first["decision"]["selected_candidate_id"] == "b"
    assert len(calls) == 1
    assert second["decision"]["selected_candidate_id"] == "b"
    assert second["cost_governor"]["reason"] == "CACHED_IDENTICAL_DECISION"


def test_live_disabled_does_not_consume_or_cache(tmp_path, monkeypatch):
    monkeypatch.delenv("JEV_SHADOW_LIVE", raising=False)
    result=evaluate_free_live_shadow(
        state={"project_id":"everward"}, candidates=CANDIDATES,
        governor_path=tmp_path/"governor.json", free_credit_available=True,
    )
    assert result["decision"]["state"] == "LIVE_DISABLED"
    assert not (tmp_path/"governor.json").exists()
