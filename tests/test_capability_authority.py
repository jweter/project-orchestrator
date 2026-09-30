import json
import pytest

from project_orchestrator.capability_authority import (
    CapabilityAuthorityError,
    compile_capability_authority,
)


def graph(repo="o/r"):
    return {
        "repository": repo,
        "capabilities": [{
            "id": "C1",
            "capability": "Explicit governed work",
            "state": "PLANNED",
            "depends_on": [],
            "completion_evidence": ["acceptance test passes"],
            "slices": [{
                "id": "S1",
                "objective": "Implement governed work",
                "acceptance": ["acceptance test passes"],
                "allowed_paths": ["src/"],
            }],
        }],
        "roadmap": ["C1"],
    }


def test_compiles_only_explicit_authoritative_machine_graph(tmp_path):
    cp={"schema_version":4,"capability_graph":"engineering/capability-graph.json",
        "authoritative_documents":["engineering/capability-graph.json","docs/ROADMAP.md"]}
    result=compile_capability_authority(
        tmp_path,"o/r","abc",cp,
        read_authoritative_file=lambda repo,head,path: json.dumps(graph(repo)),
    )
    persisted=json.loads((tmp_path/"capability-graphs"/"o__r.json").read_text())
    assert result["status"]=="COMPILED"
    assert persisted["authority"]=={
        "source":"engineering/capability-graph.json","exact_head":"abc","control_plane_schema_version":4
    }


def test_refuses_to_infer_graph_from_human_roadmap(tmp_path):
    cp={"schema_version":4,"authoritative_documents":["docs/ROADMAP.md"]}
    with pytest.raises(CapabilityAuthorityError,match="CAPABILITY_GRAPH_DECLARATION_REQUIRED"):
        compile_capability_authority(tmp_path,"o/r","abc",cp,read_authoritative_file=lambda *a:"roadmap prose")


def test_declared_graph_must_be_authoritative(tmp_path):
    cp={"schema_version":4,"capability_graph":"engineering/capability-graph.json",
        "authoritative_documents":["docs/ROADMAP.md"]}
    with pytest.raises(CapabilityAuthorityError,match="CAPABILITY_GRAPH_NOT_AUTHORITATIVE"):
        compile_capability_authority(tmp_path,"o/r","abc",cp,read_authoritative_file=lambda *a:json.dumps(graph()))


def test_repository_mismatch_fails_closed(tmp_path):
    cp={"schema_version":4,"capability_graph":"engineering/capability-graph.json",
        "authoritative_documents":["engineering/capability-graph.json"]}
    with pytest.raises(CapabilityAuthorityError,match="CAPABILITY_GRAPH_REPOSITORY_MISMATCH"):
        compile_capability_authority(tmp_path,"o/r","abc",cp,read_authoritative_file=lambda *a:json.dumps(graph("other/r")))
