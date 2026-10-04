import json
import pytest

from project_orchestrator.direction_expansion import DirectionExpansionError, compile_direction_expansion


GATE = {"next_gate": "FIRST_WORKING_BUILD", "gate": "User can perform the real workflow."}


def direction(repo="o/r", target="FIRST_WORKING_BUILD"):
    return {"repository": repo, "next_capabilities": [{
        "id": "C-next", "capability": "Complete real workflow", "target_gate": target,
        "state": "PLANNED", "depends_on": [],
        "completion_evidence": ["end-to-end acceptance passes"],
        "slices": [{"id": "S-next", "objective": "Implement workflow",
                    "acceptance": ["end-to-end acceptance passes"], "allowed_paths": ["src/"]}],
    }]}


def test_compiles_only_repository_declared_machine_direction(tmp_path):
    cp={"direction_graph":"engineering/direction-graph.json",
        "authoritative_documents":["engineering/direction-graph.json","docs/ROADMAP.md"]}
    result=compile_direction_expansion(tmp_path,"o/r","abc",cp,GATE,
        read_authoritative_file=lambda *a: json.dumps(direction()))
    assert result["status"]=="PROPOSED"
    assert result["graph"]["roadmap"]==["C-next"]
    assert result["authority"]["direction_source"]=="engineering/direction-graph.json"


def test_refuses_prose_roadmap_as_direction_authority(tmp_path):
    cp={"authoritative_documents":["docs/ROADMAP.md"]}
    with pytest.raises(DirectionExpansionError,match="DIRECTION_GRAPH_DECLARATION_REQUIRED"):
        compile_direction_expansion(tmp_path,"o/r","abc",cp,GATE,read_authoritative_file=lambda *a:"prose")


def test_direction_source_must_be_authoritative(tmp_path):
    cp={"direction_graph":"engineering/direction-graph.json","authoritative_documents":["docs/ROADMAP.md"]}
    with pytest.raises(DirectionExpansionError,match="DIRECTION_GRAPH_NOT_AUTHORITATIVE"):
        compile_direction_expansion(tmp_path,"o/r","abc",cp,GATE,read_authoritative_file=lambda *a:json.dumps(direction()))


def test_only_current_usability_gate_candidates_are_compiled(tmp_path):
    cp={"direction_graph":"engineering/direction-graph.json","authoritative_documents":["engineering/direction-graph.json"]}
    with pytest.raises(DirectionExpansionError,match="NO_CAPABILITY_FOR_CURRENT_USABILITY_GATE"):
        compile_direction_expansion(tmp_path,"o/r","abc",cp,GATE,
            read_authoritative_file=lambda *a:json.dumps(direction(target="BETA")))


def test_invalid_candidate_fails_closed(tmp_path):
    cp={"direction_graph":"engineering/direction-graph.json","authoritative_documents":["engineering/direction-graph.json"]}
    bad=direction(); del bad["next_capabilities"][0]["completion_evidence"]
    with pytest.raises(DirectionExpansionError,match="EXPANDED_CAPABILITY_GRAPH_INVALID"):
        compile_direction_expansion(tmp_path,"o/r","abc",cp,GATE,
            read_authoritative_file=lambda *a:json.dumps(bad))
