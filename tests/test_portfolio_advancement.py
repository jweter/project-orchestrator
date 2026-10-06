from project_orchestrator.portfolio_advancement import rank_usability_frontier, select_portfolio_frontier

POLICY={"products":{
 "knowledge-engine":{"next_gate":"FIRST_WORKING_BUILD"},
 "rocksmith":{"next_gate":"FIRST_WORKING_BUILD"},
 "everward":{"next_gate":"FIRST_WORKING_BUILD"},
 "voicestudio":{"next_gate":"FIRST_WORKING_BUILD"},
}}

def c(product,slice_id,**kw):
    return {"product":product,"slice_id":slice_id,"eligible":True,**kw}

def test_usability_blocker_removal_outranks_generic_work():
    rows=rank_usability_frontier([
      c("rocksmith","cleanup"),
      c("voicestudio","singing-output",removes_usability_blocker=True,advances_usability_gate=True),
    ],POLICY)
    assert rows[0]["slice_id"]=="singing-output"

def test_starvation_breaks_equal_usability_priority():
    rows=rank_usability_frontier([
      c("rocksmith","r",advances_usability_gate=True),
      c("everward","e",advances_usability_gate=True),
    ],POLICY,advancement_age={"everward":5,"rocksmith":1})
    assert rows[0]["product"]=="everward"

def test_blocked_owned_and_ineligible_candidates_never_selected():
    rows=rank_usability_frontier([
      c("rocksmith","blocked",blocked=True,removes_usability_blocker=True),
      c("everward","owned",owned=True,removes_usability_blocker=True),
      {"product":"voicestudio","slice_id":"illegal","eligible":False,"removes_usability_blocker":True},
      c("knowledge-engine","safe",advances_usability_gate=True),
    ],POLICY)
    assert [r["slice_id"] for r in rows]==["safe"]

def test_first_pass_spreads_workers_across_products():
    selected=select_portfolio_frontier([
      c("voicestudio","v1",advances_usability_gate=True),
      c("voicestudio","v2",advances_usability_gate=True),
      c("rocksmith","r1",advances_usability_gate=True),
      c("everward","e1",advances_usability_gate=True),
    ],POLICY,slots=3)
    assert len({r["product"] for r in selected})==3

def test_extra_capacity_refills_after_product_breadth():
    selected=select_portfolio_frontier([
      c("voicestudio","v1",advances_usability_gate=True),
      c("voicestudio","v2",advances_usability_gate=True),
      c("rocksmith","r1",advances_usability_gate=True),
    ],POLICY,slots=3)
    assert len(selected)==3
    assert {r["slice_id"] for r in selected}=={"v1","v2","r1"}


def test_resilient_plan_rotates_around_async_gate():
    from project_orchestrator.portfolio_advancement import build_resilient_advancement_plan
    plan=build_resilient_advancement_plan([
      c("voicestudio","ci",advances_usability_gate=True,gate_state="CI_RUNNING"),
      c("rocksmith","repair",advances_usability_gate=True),
    ],POLICY,slots=1)
    assert [r["slice_id"] for r in plan["selected"]]==["repair"]
    assert plan["waiting"][0]["rotation_reason"]=="ASYNC_GATE_IN_FLIGHT"


def test_resilient_plan_uses_next_authorized_mutation_route():
    from project_orchestrator.portfolio_advancement import build_resilient_advancement_plan
    plan=build_resilient_advancement_plan([
      c("rocksmith","repair",advances_usability_gate=True,
        authorized_mutation_routes=["primary","github","laptop"],
        failed_mutation_routes=["primary"]),
    ],POLICY,slots=1)
    assert plan["selected"][0]["mutation_route"]=="github"


def test_resilient_plan_does_not_bypass_exhausted_authority():
    from project_orchestrator.portfolio_advancement import build_resilient_advancement_plan
    plan=build_resilient_advancement_plan([
      c("everward","repair",advances_usability_gate=True,
        authorized_mutation_routes=["primary"],failed_mutation_routes=["primary"]),
      c("knowledge-engine","safe",advances_usability_gate=True),
    ],POLICY,slots=2)
    assert [r["slice_id"] for r in plan["selected"]]==["safe"]
    assert plan["route_blocked"][0]["rotation_reason"]=="AUTHORIZED_ROUTES_EXHAUSTED"


def test_resilient_plan_preserves_ineligible_and_owned_boundaries():
    from project_orchestrator.portfolio_advancement import build_resilient_advancement_plan
    plan=build_resilient_advancement_plan([
      c("rocksmith","owned",owned=True,advances_usability_gate=True),
      {"product":"everward","slice_id":"illegal","eligible":False,"advances_usability_gate":True},
      c("voicestudio","safe",advances_usability_gate=True),
    ],POLICY,slots=3)
    assert [r["slice_id"] for r in plan["selected"]]==["safe"]
