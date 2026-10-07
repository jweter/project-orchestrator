import json
from types import SimpleNamespace
from project_orchestrator.activation_capability import compile_declared_capability_graphs

def _graph(repo):
    return {"repository":repo,"capabilities":[{"id":"C1","capability":"Work","state":"PLANNED","depends_on":[],"completion_evidence":["green"],"slices":[{"id":"S1","objective":"work","acceptance":["green"],"allowed_paths":["src/"]}]}],"roadmap":["C1"]}

def test_compiles_declared_graph_at_activation_exact_head(tmp_path):
    ws=tmp_path/"repo"; ws.mkdir()
    cp={"schema_version":4,"capability_graph":"engineering/capability-graph.json","authoritative_documents":["engineering/capability-graph.json"]}
    files={"engineering/control-plane.json":json.dumps(cp),"engineering/capability-graph.json":json.dumps(_graph("o/repo"))}
    def run(argv,**kwargs):
        head,path=argv[2].split(":",1); assert head=="abc"
        return SimpleNamespace(returncode=0,stdout=files[path],stderr="")
    activation={"products":{"p":{"enabled":True,"repositories":{"o/repo":"abc"}}}}
    result=compile_declared_capability_graphs(tmp_path,tmp_path/"state",activation,run_fn=run)
    assert result["repositories"]["o/repo"]["status"]=="COMPILED"
    persisted=json.loads((tmp_path/"state"/"capability-graphs"/"o__repo.json").read_text())
    assert persisted["authority"]["exact_head"]=="abc"

def test_head_advance_removes_stale_graph_when_new_head_lacks_declaration(tmp_path):
    ws=tmp_path/"repo"; ws.mkdir()
    state=tmp_path/"state"; target=state/"capability-graphs"/"o__repo.json"
    target.parent.mkdir(parents=True); target.write_text(json.dumps({"authority":{"exact_head":"old"}}))
    def run(argv,**kwargs):
        return SimpleNamespace(returncode=0,stdout=json.dumps({"schema_version":4,"authoritative_documents":["docs/ROADMAP.md"]}),stderr="")
    activation={"products":{"p":{"enabled":True,"repositories":{"o/repo":"new"}}}}
    result=compile_declared_capability_graphs(tmp_path,state,activation,run_fn=run)
    assert result["repositories"]["o/repo"]["status"]=="DIRECTION_REQUIRED"
    assert not target.exists()

def test_git_show_timeout_fails_closed_and_removes_stale_authority(tmp_path):
    import subprocess
    ws=tmp_path/"repo"; ws.mkdir()
    state=tmp_path/"state"; target=state/"capability-graphs"/"o__repo.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"authority":{"exact_head":"old"}}))
    def run(argv,**kwargs):
        assert kwargs["timeout"] == 30
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
    activation={"products":{"p":{"enabled":True,"repositories":{"o/repo":"new"}}}}
    result=compile_declared_capability_graphs(tmp_path,state,activation,run_fn=run)
    assert result["repositories"]["o/repo"]["status"]=="DIRECTION_REQUIRED"
    assert not target.exists()