import json,subprocess
from pathlib import Path
from scripts.ci_repair_executor import execute

def repo(tmp_path):
    subprocess.run(["git","init"],cwd=tmp_path,check=True,capture_output=True)
    subprocess.run(["git","config","user.email","test@example.com"],cwd=tmp_path,check=True)
    subprocess.run(["git","config","user.name","Test"],cwd=tmp_path,check=True)
    (tmp_path/"x.py").write_text("x=1\n"); subprocess.run(["git","add","."],cwd=tmp_path,check=True)
    subprocess.run(["git","commit","-m","base"],cwd=tmp_path,check=True,capture_output=True)
    return subprocess.run(["git","rev-parse","HEAD"],cwd=tmp_path,text=True,capture_output=True,check=True).stdout.strip()

def cp(tmp_path,command=None):
    data={"deterministic_repair":{"approved_classes":["FORMAT"],"commands":{"FORMAT":[command] if command else []}}}
    p=tmp_path/"cp.json";p.write_text(json.dumps(data))
    subprocess.run(["git","add","cp.json"],cwd=tmp_path,check=True)
    subprocess.run(["git","commit","-m","policy"],cwd=tmp_path,check=True,capture_output=True)
    return p

def packet(tmp_path):
    return {"head_sha":subprocess.run(["git","rev-parse","HEAD"],cwd=tmp_path,text=True,capture_output=True,check=True).stdout.strip(),
            "repository":"o/r","first_meaningful_failure":"1 file would be reformatted"}

def test_stale_head_fails_closed(tmp_path):
    repo(tmp_path); p={"head_sha":"0"*40,"repository":"o/r","first_meaningful_failure":"would be reformatted"}
    assert execute(p,tmp_path,cp(tmp_path),["python","-c","raise SystemExit(0)"])["status"]=="STALE_HEAD"

def test_missing_repository_authority_fails_closed(tmp_path):
    repo(tmp_path); policy=cp(tmp_path); p=packet(tmp_path)
    assert execute(p,tmp_path,policy,["python","-c","raise SystemExit(0)"])["status"]=="REPAIR_NOT_AUTHORIZED"

def test_authorized_repair_must_pass_full_preflight(tmp_path):
    repo(tmp_path); cmd=["python","-c","from pathlib import Path; Path('x.py').write_text('x = 1\\n')"]
    policy=cp(tmp_path,cmd); p=packet(tmp_path)
    out=execute(p,tmp_path,policy,["python","-c","raise SystemExit(0)"])
    assert out["status"]=="REPAIR_VERIFIED"; assert out["changed_files"]==["x.py"]

def test_failed_preflight_blocks_repair(tmp_path):
    repo(tmp_path); cmd=["python","-c","from pathlib import Path; Path('x.py').write_text('x = 1\\n')"]
    policy=cp(tmp_path,cmd); p=packet(tmp_path)
    assert execute(p,tmp_path,policy,["python","-c","raise SystemExit(1)"])["status"]=="PREFLIGHT_FAILED"
