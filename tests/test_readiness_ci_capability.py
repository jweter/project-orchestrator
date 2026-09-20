import json, subprocess
from pathlib import Path
from scripts.hands_off_readiness import audit

def _repo(tmp_path:Path):
    subprocess.run(["git","init"],cwd=tmp_path,check=True,capture_output=True)
    subprocess.run(["git","config","user.email","test@example.com"],cwd=tmp_path,check=True)
    subprocess.run(["git","config","user.name","Test"],cwd=tmp_path,check=True)
    subprocess.run(["git","remote","add","origin","https://github.com/example/repo.git"],cwd=tmp_path,check=True)

def test_ci_authority_accepts_real_tracked_workflow_not_filename_convention(tmp_path):
    _repo(tmp_path)
    for rel,text in {
        "README.md":"purpose security privacy license provenance Jeremy Product Reality",
        "docs/ROADMAP.md":"roadmap",
        "docs/ARCHITECTURE.md":"architecture",
        "AGENTS.md":"agents",
        "engineering/control-plane.json":"{}",
        "engineering/preflight.py":"pass",
        ".github/workflows/quality.yml":"name: quality\non: [push]\njobs: {}",
    }.items():
        p=tmp_path/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
    subprocess.run(["git","add","."],cwd=tmp_path,check=True)
    subprocess.run(["git","commit","-m","fixture"],cwd=tmp_path,check=True,capture_output=True)
    contract={"required_authority":[{"id":"ci","evidence_any":[".github/workflows/ci.yml",".github/workflows/ci.yaml"]}]}
    result=audit(tmp_path,contract)
    check=result["checks"][0]
    assert check["status"]=="PASS"
    assert check["evidence"]==[".github/workflows/quality.yml"]

def test_untracked_workflow_cannot_satisfy_ci_authority(tmp_path):
    _repo(tmp_path)
    (tmp_path/"README.md").write_text("purpose")
    subprocess.run(["git","add","README.md"],cwd=tmp_path,check=True)
    subprocess.run(["git","commit","-m","fixture"],cwd=tmp_path,check=True,capture_output=True)
    p=tmp_path/".github/workflows/quality.yml";p.parent.mkdir(parents=True);p.write_text("name: quality")
    contract={"required_authority":[{"id":"ci","evidence_any":[".github/workflows/ci.yml"]}]}
    assert audit(tmp_path,contract)["checks"][0]["status"]=="FAIL"
