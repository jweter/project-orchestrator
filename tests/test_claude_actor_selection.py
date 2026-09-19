import json
from project_orchestrator.claude_dispatch import select_actor, dispatch_if_warranted

class Response:
    def __enter__(self): return self
    def __exit__(self,*a): return False
    def read(self): return json.dumps({"claude_code_session_id":"cse_routed","claude_code_session_url":"https://claude.ai/code/cse_routed"}).encode()

def test_local_verified_capability_beats_claude():
    d=select_actor({"kind":"DETERMINISTIC_FAILURE","local_capability_verified":True})
    assert d["actor"] == "LAPTOP_ACTOR"

def test_deep_problem_routes_to_deep_specialist():
    d=select_actor({"kind":"DEEP_ARCHITECTURE"})
    assert (d["actor"],d["routine"]) == ("CLAUDE_CLOUD","deep_product")

def test_bad_specialist_is_demoted():
    scores={"portfolio_builder":{"completed":4,"role_adherence_rate":0.5,"human_intervention_rate":0.0}}
    d=select_actor({"kind":"PRODUCT_SLICE"},scores)
    assert d["actor"] == "LAPTOP_ACTOR"

def test_human_boundary_never_dispatched():
    assert select_actor({"kind":"DEEP_ARCHITECTURE","requires_human":True})["actor"] == "HUMAN"

def test_dispatch_if_warranted_records_routed_session(tmp_path,monkeypatch):
    monkeypatch.setenv("CLAUDE_ROUTINE_ADVERSARIAL_REVIEWER_TOKEN","secret")
    task={"kind":"RECENT_CHANGE_REVIEW","repository":"jweter/x","task_key":"pr-7","prompt":"review"}
    out=dispatch_if_warranted(task,tmp_path,opener=lambda *a,**k: Response())
    assert out["dispatched"] is True
    assert out["routine"] == "adversarial_reviewer"
    assert out["session_id"] == "cse_routed"
