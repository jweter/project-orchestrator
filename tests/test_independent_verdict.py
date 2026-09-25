import subprocess
from pathlib import Path
from project_orchestrator.independent_verdict import review_candidate

def repo(tmp_path):
    subprocess.run(["git","init"],cwd=tmp_path,check=True,capture_output=True)
    subprocess.run(["git","config","user.email","test@example.com"],cwd=tmp_path,check=True)
    subprocess.run(["git","config","user.name","Test"],cwd=tmp_path,check=True)
    (tmp_path/"src").mkdir(); (tmp_path/"src/a.py").write_text("x=1\n")
    subprocess.run(["git","add","."],cwd=tmp_path,check=True); subprocess.run(["git","commit","-m","base"],cwd=tmp_path,check=True,capture_output=True)
    base=subprocess.check_output(["git","rev-parse","HEAD"],cwd=tmp_path,text=True).strip()
    (tmp_path/"src/a.py").write_text("x=2\n")
    subprocess.run(["git","add","."],cwd=tmp_path,check=True); subprocess.run(["git","commit","-m","change"],cwd=tmp_path,check=True,capture_output=True)
    head=subprocess.check_output(["git","rev-parse","HEAD"],cwd=tmp_path,text=True).strip()
    return base,head

def authority():
    return {"acceptance":["tests green"],"allowed_paths":["src"],"forbidden_paths":["secrets"]}

def test_independent_reviewer_approves_proven_candidate(tmp_path):
    base,head=repo(tmp_path)
    assert review_candidate(tmp_path,base,head,authority(),["src/a.py"],0).status=="APPROVE"

def test_independent_reviewer_rejects_head_mismatch(tmp_path):
    base,head=repo(tmp_path)
    v=review_candidate(tmp_path,base,"0"*40,authority(),["src/a.py"],0)
    assert v.reason=="HEAD_PROVENANCE_MISMATCH"

def test_independent_reviewer_rejects_scope_violation(tmp_path):
    base,head=repo(tmp_path)
    v=review_candidate(tmp_path,base,head,{**authority(),"allowed_paths":["tests"]},["src/a.py"],0)
    assert v.reason.startswith("OUTSIDE_AUTHORITY:")

def test_independent_reviewer_requires_acceptance_and_green_preflight(tmp_path):
    base,head=repo(tmp_path)
    assert review_candidate(tmp_path,base,head,{**authority(),"acceptance":[]},["src/a.py"],0).reason=="ACCEPTANCE_AUTHORITY_INVALID"
    assert review_candidate(tmp_path,base,head,authority(),["src/a.py"],1).reason=="PREFLIGHT_NOT_GREEN"
