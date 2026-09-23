import hashlib, json
from scripts import continuous_flow_supervisor as supervisor

def _activation(repo="jweter/example",head="abc"):
    return {"products":{"example":{"repositories":{repo:head}}}}

def _graph():
    return {"capabilities":[
        {"id":"C1","capability":"parser","state":"PLANNED","depends_on":[],"completion_evidence":["tests"],
         "slices":[{"id":"S1","state":"PLANNED","objective":"Implement parser",
                    "acceptance":["parser tests pass"],"allowed_paths":["src/"]}]}
    ],"roadmap":["C1"]}

def test_execution_grant_contains_roadmap_derived_exact_head_slice(tmp_path):
    repo_root=tmp_path/"control"; state=tmp_path/"state"
    (repo_root/"engineering").mkdir(parents=True); state.mkdir()
    (repo_root/"engineering"/"hands-off-evolution.json").write_text(json.dumps({"products":{"example":["jweter/example"]}}))
    activation=_activation()
    (state/"hands-off-activation.json").write_text(json.dumps(activation))
    graphs=state/"capability-graphs"; graphs.mkdir()
    (graphs/"jweter__example.json").write_text(json.dumps(_graph()))
    path=supervisor.write_execution_grant(repo_root,state,["example"])
    grant=json.loads(path.read_text())
    assert grant["execution_slice"]["capability_id"]=="C1"
    assert grant["execution_slice"]["slice_id"]=="S1"
    assert grant["execution_slice"]["repository"]=="jweter/example"
    assert grant["execution_slice"]["exact_head"]=="abc"
    assert grant["execution_slice"]["objective"]=="Implement parser"

def test_execution_grant_fails_closed_without_capability_graph(tmp_path):
    repo_root=tmp_path/"control"; state=tmp_path/"state"
    (repo_root/"engineering").mkdir(parents=True); state.mkdir()
    (repo_root/"engineering"/"hands-off-evolution.json").write_text(json.dumps({"products":{"example":["jweter/example"]}}))
    (state/"hands-off-activation.json").write_text(json.dumps(_activation()))
    try:
        supervisor.write_execution_grant(repo_root,state,["example"])
    except RuntimeError as exc:
        assert "CAPABILITY_GRAPH_REQUIRED:jweter/example" in str(exc)
    else:
        raise AssertionError("grant issuance must fail closed without product authority")

def test_multi_repository_product_grant_selects_only_ready_lane(tmp_path):
    repo_root=tmp_path/"control"; state=tmp_path/"state"
    (repo_root/"engineering").mkdir(parents=True); state.mkdir()
    (repo_root/"engineering"/"hands-off-evolution.json").write_text(json.dumps({"products":{"example":["jweter/a","jweter/b"]}}))
    activation={"products":{"example":{"repositories":{"jweter/a":"a","jweter/b":"b"}}}}
    (state/"hands-off-activation.json").write_text(json.dumps(activation))
    graphs=state/"capability-graphs"; graphs.mkdir()
    (graphs/"jweter__b.json").write_text(json.dumps(_graph()))
    grant=json.loads(supervisor.write_execution_grant(repo_root,state,["example"]).read_text())
    assert grant["products"]=={"example":{"repositories":{"jweter/a":"a","jweter/b":"b"}}}
    assert grant["execution_slice"]["repository"]=="jweter/b"
    assert grant["execution_slice"]["exact_head"]=="b"

def test_execution_target_rotates_across_ready_repositories(tmp_path):
    state=tmp_path/"state"; state.mkdir()
    graphs=state/"capability-graphs"; graphs.mkdir()
    (graphs/"jweter__a.json").write_text(json.dumps(_graph()))
    (graphs/"jweter__b.json").write_text(json.dumps(_graph()))
    activation={"products":{"example":{"repositories":{"jweter/a":"a","jweter/b":"b"}}}}
    first=supervisor.select_execution_target(state,activation,["example"])
    second=supervisor.select_execution_target(state,activation,["example"])
    assert first==("jweter/a","a")
    assert second==("jweter/b","b")
