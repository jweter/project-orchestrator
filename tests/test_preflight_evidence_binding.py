import json,subprocess
from project_orchestrator.independent_verdict import bind_preflight_evidence,review_candidate

def setup_repo(tmp_path):
    subprocess.run(["git","init"],cwd=tmp_path,check=True,capture_output=True)
    subprocess.run(["git","config","user.email","t@e"],cwd=tmp_path,check=True)
    subprocess.run(["git","config","user.name","T"],cwd=tmp_path,check=True)
    (tmp_path/"src").mkdir(); (tmp_path/"src/a.py").write_text("x=1\n")
    subprocess.run(["git","add","."],cwd=tmp_path,check=True); subprocess.run(["git","commit","-m","b"],cwd=tmp_path,check=True,capture_output=True)
    base=subprocess.check_output(["git","rev-parse","HEAD"],cwd=tmp_path,text=True).strip()
    (tmp_path/"src/a.py").write_text("x=2\n")
    subprocess.run(["git","add","."],cwd=tmp_path,check=True); subprocess.run(["git","commit","-m","h"],cwd=tmp_path,check=True,capture_output=True)
    head=subprocess.check_output(["git","rev-parse","HEAD"],cwd=tmp_path,text=True).strip()
    (tmp_path/"preflight-evidence.json").write_text(json.dumps({"schema_version":2,"status":"GREEN","head_sha":head}))
    return base,head

def auth(): return {"acceptance":["green"],"allowed_paths":["src"],"forbidden_paths":[]}

def test_bound_evidence_approves_exact_candidate(tmp_path):
    base,head=setup_repo(tmp_path); binding=bind_preflight_evidence(tmp_path,base,head,"g1")
    assert review_candidate(tmp_path,base,head,auth(),["src/a.py"],0,binding,"g1").status=="APPROVE"

def test_binding_rejects_other_grant(tmp_path):
    base,head=setup_repo(tmp_path); binding=bind_preflight_evidence(tmp_path,base,head,"g1")
    assert review_candidate(tmp_path,base,head,auth(),["src/a.py"],0,binding,"g2").reason=="PREFLIGHT_EVIDENCE_BINDING_MISMATCH"

def test_binding_rejects_evidence_tampering(tmp_path):
    base,head=setup_repo(tmp_path); binding=bind_preflight_evidence(tmp_path,base,head,"g1")
    (tmp_path/"preflight-evidence.json").write_text(json.dumps({"status":"GREEN","tampered":True}))
    assert review_candidate(tmp_path,base,head,auth(),["src/a.py"],0,binding,"g1").reason=="PREFLIGHT_EVIDENCE_BINDING_MISMATCH"
