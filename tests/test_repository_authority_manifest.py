import json, subprocess
from pathlib import Path
from scripts.hands_off_readiness import audit

def _init(repo:Path):
    subprocess.run(["git","init"],cwd=repo,check=True,capture_output=True)
    subprocess.run(["git","config","user.email","test@example.com"],cwd=repo,check=True)
    subprocess.run(["git","config","user.name","Test"],cwd=repo,check=True)
    subprocess.run(["git","remote","add","origin","https://github.com/example/repo.git"],cwd=repo,check=True)

def _write(repo:Path,rel:str,text:str):
    p=repo/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)

def test_explicit_tracked_authority_paths_satisfy_noncanonical_names(tmp_path):
    _init(tmp_path)
    _write(tmp_path,"README.md","purpose")
    _write(tmp_path,"docs/roadmap.md","authoritative unfinished roadmap")
    _write(tmp_path,"docs/architecture.md","architecture boundaries")
    _write(tmp_path,"engineering/hands-off-authority.json",json.dumps({"schema_version":1,"authority":{
        "vision_completion":["README.md"],"roadmap":["docs/roadmap.md"],"architecture":["docs/architecture.md"]}}))
    subprocess.run(["git","add","."],cwd=tmp_path,check=True);subprocess.run(["git","commit","-m","fixture"],cwd=tmp_path,check=True,capture_output=True)
    contract={"required_authority":[
        {"id":"vision_completion","evidence_any":["README.md"]},
        {"id":"roadmap","evidence_any":["ROADMAP.md"]},
        {"id":"architecture","evidence_any":["ARCHITECTURE.md"]}]}
    result=audit(tmp_path,contract)
    assert result["status"]=="PASS"
    assert result["alignment"]["status"]=="ALIGNED"
    assert result["checks"][1]["evidence"]==["docs/roadmap.md"]

def test_manifest_cannot_authorize_untracked_path(tmp_path):
    _init(tmp_path);_write(tmp_path,"README.md","purpose")
    _write(tmp_path,"engineering/hands-off-authority.json",json.dumps({"schema_version":1,"authority":{"roadmap":["docs/private-roadmap.md"]}}))
    _write(tmp_path,"docs/private-roadmap.md","not tracked")
    subprocess.run(["git","add","README.md","engineering/hands-off-authority.json"],cwd=tmp_path,check=True);subprocess.run(["git","commit","-m","fixture"],cwd=tmp_path,check=True,capture_output=True)
    result=audit(tmp_path,{"required_authority":[{"id":"roadmap","evidence_any":["ROADMAP.md"]}]})
    assert result["checks"][0]["status"]=="FAIL"
    assert result["alignment"]["status"]=="ALIGNMENT_INSUFFICIENT"

def test_malformed_manifest_fails_closed(tmp_path):
    _init(tmp_path);_write(tmp_path,"README.md","purpose");_write(tmp_path,"engineering/hands-off-authority.json","not json")
    subprocess.run(["git","add","."],cwd=tmp_path,check=True);subprocess.run(["git","commit","-m","fixture"],cwd=tmp_path,check=True,capture_output=True)
    result=audit(tmp_path,{"required_authority":[]})
    assert result["status"]=="UNKNOWN"
    assert "invalid repository authority manifest" in result["error"]
