from datetime import datetime, timezone
from copy import deepcopy
from project_orchestrator.closed_loop import validate_closed_loop_run
from tests.test_closed_loop import payload as make_payload

def test_closed_loop_selects_claude_specialist_before_act():
    payload=make_payload()
    payload["projects"]["knowledge-engine"]["actor_task"]={
        "kind":"DEEP_ARCHITECTURE","repository":"jweter/knowledge-engine-core","task_key":"issue-129"}
    out=validate_closed_loop_run(payload,now=datetime.now(timezone.utc))
    sel=out["projects"]["knowledge-engine"]["actor_selection"]
    assert sel["actor"]=="CLAUDE_CLOUD"
    assert sel["routine"]=="deep_product"

def test_closed_loop_prefers_verified_local_capability():
    payload=make_payload()
    payload["projects"]["rocksmith"]["actor_task"]={
        "kind":"DETERMINISTIC_FAILURE","local_capability_verified":True,
        "repository":"jweter/rocksmith-cdlc-generator","task_key":"worker-1"}
    out=validate_closed_loop_run(payload,now=datetime.now(timezone.utc))
    assert out["projects"]["rocksmith"]["actor_selection"]["actor"]=="LAPTOP_ACTOR"

def test_closed_loop_demotes_measured_bad_claude_specialist():
    payload=make_payload()
    payload["projects"]["everward"]["actor_task"]={
        "kind":"PRODUCT_SLICE","repository":"jweter/Project-Everward","task_key":"slice-1"}
    scores={"portfolio_builder":{"completed":4,"role_adherence_rate":0.5,"human_intervention_rate":0.0}}
    out=validate_closed_loop_run(payload,now=datetime.now(timezone.utc),actor_scorecard=scores)
    assert out["projects"]["everward"]["actor_selection"]["actor"]=="LAPTOP_ACTOR"
