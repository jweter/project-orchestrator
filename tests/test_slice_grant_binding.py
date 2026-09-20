import json
from pathlib import Path
from scripts.autonomous_slice_builder import execution_slice_authority, prompt_for_slice

def grant(tmp_path:Path,authority:dict):
    p=tmp_path/"grant.json"
    p.write_text(json.dumps({"execution_slice":authority}),encoding="utf-8")
    return p

def authority():
    return {"capability_id":"C1","slice_id":"S1","repository":"o/r","exact_head":"abc",
            "objective":"Implement parser","acceptance":["parser tests pass"],
            "allowed_paths":["src/","tests/"],"forbidden_paths":["secrets/"]}

def test_exact_repo_head_returns_slice_authority(tmp_path):
    a=authority()
    assert execution_slice_authority(grant(tmp_path,a),"o/r","abc")==a

def test_head_drift_revokes_slice_authority(tmp_path):
    assert execution_slice_authority(grant(tmp_path,authority()),"o/r","def") is None

def test_repository_mismatch_revokes_slice_authority(tmp_path):
    assert execution_slice_authority(grant(tmp_path,authority()),"o/other","abc") is None

def test_incomplete_slice_authority_fails_closed(tmp_path):
    a=authority();a["acceptance"]=[]
    assert execution_slice_authority(grant(tmp_path,a),"o/r","abc") is None

def test_slice_prompt_cannot_invite_roadmap_task_selection(tmp_path):
    p=prompt_for_slice("o/r",authority(),tmp_path/"state.json")
    assert "Execute exactly this authorized bounded slice" in p
    assert "Do not substitute another roadmap task" in p
    assert "Objective: Implement parser" in p
    assert "parser tests pass" in p
