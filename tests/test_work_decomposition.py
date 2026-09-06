import pytest
from project_orchestrator.work_decomposition import decompose_work_item

def base():
    return {"project_id":"everward","work_id":"slice8","title":"zero-g","slices":[
      {"slice_id":"contract","repository":"jweter/Project-Everward","title":"contract","paths":["docs/zero-g.md"],"dependencies":[],"verification_weight":1,"reversibility":"HIGH"},
      {"slice_id":"scene","repository":"jweter/Project-Everward","title":"scene","paths":["Source/ZeroG"],"dependencies":["contract"],"verification_weight":3,"reversibility":"MEDIUM"},
      {"slice_id":"ui","repository":"jweter/Project-Everward","title":"ui","paths":["Source/UI"],"dependencies":["contract"],"verification_weight":2,"reversibility":"HIGH"}]}

def test_dependency_levels_and_waves():
    p=decompose_work_item(base())
    assert p["recommended_parallel_waves"][0]==["contract"]
    assert all("not duration/ROI/probability" in s["score_semantics"] for s in p["slices"])

def test_active_overlap_blocks():
    snap={"repositories":[{"repository":"jweter/Project-Everward","active_work":[{"branch":"orchestrator/ui","changed_files":["Source/UI"]}]}]}
    p=decompose_work_item(base(),live_snapshot=snap)
    assert next(s for s in p["slices"] if s["slice_id"]=="ui")["state"]=="BLOCKED"

def test_cycle_fails_closed():
    x=base(); x["slices"][0]["dependencies"]=["scene"]
    with pytest.raises(ValueError,match="cycle"): decompose_work_item(x)

def test_ke_shared_contract_requires_clearance():
    x={"project_id":"knowledge-engine","work_id":"x","title":"x","slices":[{"slice_id":"core","repository":"jweter/knowledge-engine-core","title":"x","paths":["schema.py"],"dependencies":[],"shared_contract_change":True,"compatibility_plan_clear":False}]}
    assert decompose_work_item(x)["slices"][0]["state"]=="BLOCKED"

def test_product_reality_dependency_fails_closed():
    x=base(); x["slices"][1]["requires_product_reality_result"]=True
    assert next(s for s in decompose_work_item(x)["slices"] if s["slice_id"]=="scene")["state"]=="BLOCKED"

def test_unknown_dependency_rejected():
    x=base(); x["slices"][0]["dependencies"]=["missing"]
    with pytest.raises(ValueError,match="unknown dependencies"): decompose_work_item(x)
