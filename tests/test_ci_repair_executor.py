import json,subprocess
from scripts.ci_repair_executor import execute

def setup(tmp_path,command=None):
    subprocess.run(["git","init"],cwd=tmp_path,check=True,capture_output=True);subprocess.run(["git","config","user.email","t@e"],cwd=tmp_path,check=True);subprocess.run(["git","config","user.name","T"],cwd=tmp_path,check=True)
    (tmp_path/"x.py").write_text("x=1\n")
    data={"preflight":{"executable":"engineering/preflight.py"},"deterministic_repair":{"approved_classes":["FORMAT"],"commands":{"FORMAT":[command] if command else []}}}
    (tmp_path/"cp.json").write_text(json.dumps(data));subprocess.run(["git","add","."],cwd=tmp_path,check=True);subprocess.run(["git","commit","-m","base"],cwd=tmp_path,check=True,capture_output=True)
    return subprocess.run(["git","rev-parse","HEAD"],cwd=tmp_path,text=True,capture_output=True,check=True).stdout.strip()

def packet(head,ancestry=None):return {"head_sha":head,"repository":"o/r","failure_fingerprint":"fp1","first_meaningful_failure":"1 file would be reformatted","repair_ancestry":ancestry or []}
def cmd():return ["python","-c","from pathlib import Path; Path('x.py').write_text('x = 1\\n')"]

def test_stale_head_fails_closed(tmp_path):
    setup(tmp_path);assert execute(packet("0"*40),tmp_path,tmp_path/"cp.json")["status"]=="STALE_HEAD"
def test_dirty_policy_cannot_grant_authority(tmp_path):
    h=setup(tmp_path);(tmp_path/"cp.json").write_text(json.dumps({"deterministic_repair":{"approved_classes":["FORMAT"],"commands":{"FORMAT":[cmd()]}}}))
    assert execute(packet(h),tmp_path,tmp_path/"cp.json")["status"]=="DIRTY_WORKTREE"
def test_missing_repository_authority_fails_closed(tmp_path):
    h=setup(tmp_path);assert execute(packet(h),tmp_path,tmp_path/"cp.json")["status"]=="REPAIR_NOT_AUTHORIZED"
def test_applied_repair_requires_independent_verification(tmp_path):
    h=setup(tmp_path,cmd());out=execute(packet(h),tmp_path,tmp_path/"cp.json")
    assert out["status"]=="VERIFY_REPAIR" and out["fail_closed"] is True and out["canonical_preflight"]=="engineering/preflight.py"
def test_repair_iteration_derived_from_ancestry(tmp_path):
    h=setup(tmp_path,cmd());a=[{"repository":"o/r","failure_fingerprint":"fp1"}]*3
    out=execute(packet(h,a),tmp_path,tmp_path/"cp.json");assert out["status"]=="REPAIR_BUDGET_EXHAUSTED" and out["iteration"]==4
def test_invalid_ancestry_fails_closed(tmp_path):
    h=setup(tmp_path,cmd());p=packet(h);p["repair_ancestry"]="bad"
    assert execute(p,tmp_path,tmp_path/"cp.json")["status"]=="INVALID_REPAIR_ANCESTRY"
