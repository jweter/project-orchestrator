"""Apply repository-authorized deterministic CI repair; verification is independent."""
from __future__ import annotations
import argparse,json,subprocess
from pathlib import Path
from project_orchestrator.repair import build_repair_plan,execute_repair_plan,load_control_plane,MAX_REPAIR_ITERATIONS

def run(argv,cwd,check=False): return subprocess.run(argv,cwd=cwd,text=True,capture_output=True,check=check)
def sha(root): return run(["git","rev-parse","HEAD"],root,True).stdout.strip()
def clean(root): return not run(["git","status","--porcelain"],root,True).stdout.strip()

def execute(packet:dict,repo_root:Path,control_plane_path:Path)->dict:
    expected=packet.get("head_sha")
    if not expected or sha(repo_root)!=expected:return {"status":"STALE_HEAD","fail_closed":True}
    if not clean(repo_root):return {"status":"DIRTY_WORKTREE","fail_closed":True}
    cp=load_control_plane(control_plane_path)
    ancestry=packet.get("repair_ancestry",[])
    if not isinstance(ancestry,list) or any(not isinstance(x,dict) for x in ancestry):
        return {"status":"INVALID_REPAIR_ANCESTRY","fail_closed":True}
    prior=[x for x in ancestry if x.get("repository")==packet.get("repository") and x.get("failure_fingerprint")==packet.get("failure_fingerprint")]
    iteration=len(prior)+1
    if iteration>MAX_REPAIR_ITERATIONS:return {"status":"REPAIR_BUDGET_EXHAUSTED","fail_closed":True,"iteration":iteration}
    plan=build_repair_plan(repository=packet.get("repository",""),log_text=packet.get("first_meaningful_failure",""),control_plane=cp,iteration=iteration)
    if not plan["eligible_for_auto_repair"]:return {"status":"REPAIR_NOT_AUTHORIZED","fail_closed":True,"plan":plan}
    result=execute_repair_plan(plan,repo_root=repo_root)
    if result["execution_status"]!="REPAIR_COMMANDS_APPLIED":return {"status":"REPAIR_COMMAND_FAILED","fail_closed":True,"plan":result}
    if clean(repo_root):return {"status":"REPAIR_NO_CHANGE","fail_closed":True,"plan":result}
    if sha(repo_root)!=expected:return {"status":"HEAD_MOVED","fail_closed":True,"plan":result}
    return {"status":"VERIFY_REPAIR","fail_closed":True,"plan":result,"expected_head_sha":expected,
            "iteration":iteration,"changed_files":run(["git","diff","--name-only"],repo_root,True).stdout.splitlines(),
            "verification_required":True,"canonical_preflight":cp.get("preflight",{}).get("executable")}

def main():
    p=argparse.ArgumentParser();p.add_argument("--packet",type=Path,required=True);p.add_argument("--repo-root",type=Path,required=True);p.add_argument("--control-plane",type=Path,required=True);p.add_argument("--output",type=Path)
    a=p.parse_args();out=execute(json.loads(a.packet.read_text()),a.repo_root,a.control_plane);s=json.dumps(out,indent=2,sort_keys=True)+"\n"
    if a.output:a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(s)
    print(s,end="");return 0 if out["status"]=="VERIFY_REPAIR" else 2
if __name__=="__main__":raise SystemExit(main())
