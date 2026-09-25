import json
from scripts.capability_slice_contract import derive_slices
import scripts.continuous_flow_supervisor as supervisor

def graph():
    return {"roadmap":["cap"],"capabilities":[{
        "id":"cap","capability":"x","state":"PLANNED","completion_evidence":["test"],
        "depends_on":[],"slices":[{
            "id":"s","objective":"build","acceptance":["green"],"state":"PLANNED",
            "allowed_paths":["src/**"],"runner_requirements":["code","local"],
            "runtime_requirements":["ollama"]}]}]}

def test_requirements_survive_graph_to_derived_slice():
    task=derive_slices(graph(),repository="jweter/repo",exact_head="abc")["slices"][0]
    assert task["runner_requirements"]==["code","local"]
    assert task["runtime_requirements"]==["ollama"]

def test_legacy_slice_gets_explicit_safe_defaults():
    payload=graph(); s=payload["capabilities"][0]["slices"][0]
    s.pop("runner_requirements"); s.pop("runtime_requirements")
    task=derive_slices(payload,repository="jweter/repo",exact_head="abc")["slices"][0]
    assert task["runner_requirements"]==["code","local","windows","offline"]
    assert task["runtime_requirements"]==[]

def test_requirements_drive_runner_runtime_and_resource_selection(monkeypatch):
    task=derive_slices(graph(),repository="jweter/repo",exact_head="abc")["slices"][0]
    assert supervisor.runner_requirements_for_task(task)=={"code","local"}
    monkeypatch.setattr(supervisor,"require_runtime_capabilities",lambda rid,req:None)
    assert supervisor.runtime_for_task(task)=="laptop-actor"
    assert supervisor.resource_leases_for_task(task,"laptop-actor")==("resource:ollama",)
