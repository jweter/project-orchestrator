from project_orchestrator.specialist_lanes import build_lane_plan,validate_lane_result

INV={"projects":[
 {"id":"knowledge-engine","repositories":[{"name":"jweter/knowledge-engine-core"},{"name":"jweter/knowledge-engine-web"},{"name":"jweter/knowledge-engine-ai"}]},
 {"id":"rocksmith","repositories":[{"name":"jweter/rocksmith-cdlc-generator"}]},
 {"id":"everward","repositories":[{"name":"jweter/Project-Everward"}]},
]}
SNAP={"repositories":[
 {"repository":"jweter/knowledge-engine-core","default_head_sha":"c","active_work":[{"branch":"orchestrator/core","issue":449,"changed_files":["knowledge_engine/extraction"]}]},
 {"repository":"jweter/knowledge-engine-web","default_head_sha":"w","active_work":[]},
 {"repository":"jweter/knowledge-engine-ai","default_head_sha":"a","active_work":[]},
 {"repository":"jweter/rocksmith-cdlc-generator","default_head_sha":"r","active_work":[]},
 {"repository":"jweter/Project-Everward","default_head_sha":"e","active_work":[]},
]}


def test_independent_projects_can_reserve_concurrently():
    p=build_lane_plan(INV,SNAP,[
      {"project_id":"rocksmith","repository":"jweter/rocksmith-cdlc-generator","base_sha":"r","branch":"orchestrator/r","title":"r","priority":5,"paths":["src/r.py"]},
      {"project_id":"everward","repository":"jweter/Project-Everward","base_sha":"e","branch":"orchestrator/e","title":"e","priority":5,"paths":["src/e.cpp"]},
    ])
    assert [x["state"] for x in p["lanes"]]==["RESERVED","RESERVED"]
    assert all(x["merge_authority"] is False for x in p["lanes"])


def test_active_overlap_blocks_duplicate_lane():
    p=build_lane_plan(INV,SNAP,[{"project_id":"knowledge-engine","repository":"jweter/knowledge-engine-core","base_sha":"c","branch":"orchestrator/new","title":"x","priority":4,"paths":["knowledge_engine/extraction/a.py"]}])
    assert p["lanes"][0]["state"]=="BLOCKED"


def test_existing_owned_branch_can_continue():
    p=build_lane_plan(INV,SNAP,[{"project_id":"knowledge-engine","repository":"jweter/knowledge-engine-core","base_sha":"c","branch":"orchestrator/core","issue":449,"title":"x","priority":4,"paths":["knowledge_engine/extraction/a.py"]}])
    assert p["lanes"][0]["state"]=="RESERVED"


def test_ke_shared_contract_requires_compatibility_clearance():
    p=build_lane_plan(INV,SNAP,[{"project_id":"knowledge-engine","repository":"jweter/knowledge-engine-web","base_sha":"w","branch":"orchestrator/w","title":"x","priority":5,"shared_contract_change":True,"compatibility_plan_clear":False}])
    assert p["lanes"][0]["state"]=="BLOCKED"


def test_stale_base_fails_closed():
    p=build_lane_plan(INV,SNAP,[{"project_id":"everward","repository":"jweter/Project-Everward","base_sha":"old","branch":"orchestrator/e","title":"x","priority":5}])
    assert p["lanes"][0]["state"]=="STALE"


def test_one_substantial_slice_per_project():
    p=build_lane_plan(INV,SNAP,[
      {"project_id":"knowledge-engine","repository":"jweter/knowledge-engine-web","base_sha":"w","branch":"orchestrator/w","title":"w","priority":5},
      {"project_id":"knowledge-engine","repository":"jweter/knowledge-engine-ai","base_sha":"a","branch":"orchestrator/a","title":"a","priority":6},
    ])
    assert [x["state"] for x in p["lanes"]].count("RESERVED")==1


def test_result_requires_green_preflight_for_promotion():
    result={"lane_id":"x","project_id":"everward","repository":"jweter/Project-Everward","base_sha":"e","head_sha":"h","changed_files":["a"],"preflight_state":"FAILED","dependency_impact":"none","product_reality_debt":"pending","continuation":"repair"}
    assert validate_lane_result(result,live_default_head="e")["state"]=="PREFLIGHT_PENDING"


def test_result_becomes_stale_when_default_head_moves():
    result={"lane_id":"x","project_id":"rocksmith","repository":"jweter/rocksmith-cdlc-generator","base_sha":"r","head_sha":"h","changed_files":["a"],"preflight_state":"PREFLIGHT_GREEN","dependency_impact":"none","product_reality_debt":"pending","continuation":"open pr"}
    assert validate_lane_result(result,live_default_head="new")["state"]=="STALE"
