"""Execute a fail-closed deterministic CI repair against an exact PR head."""
from __future__ import annotations
import argparse,json,subprocess
from pathlib import Path
from project_orchestrator.repair import build_repair_plan,execute_repair_plan,load_control_plane

def run(argv,cwd,check=False):
    return subprocess.run(argv,cwd=cwd,text=True,capture_output=True,check=check)

def sha(root): return run(["git","rev-parse","HEAD"],root,True).stdout.strip()
def clean(root): return not run(["git","status","--porcelain"],root,True).stdout.strip()

def execute(packet:dict,repo_root:Path,control_plane_path:Path,preflight:list[str])->dict:
    expected=packet.get("head_sha")
    if not expected or sha(repo_root)!=expected:
        return {"status":"STALE_HEAD","fail_closed":True}
    dirty=run(["git","status","--porcelain"],repo_root,True).stdout.splitlines()
    policy_rel=str(control_plane_path.resolve().relative_to(repo_root.resolve())).replace("\\","/")
    unrelated=[line for line in dirty if line[3:].replace("\\","/")!=policy_rel]
    if unrelated:
        return {"status":"DIRTY_WORKTREE","fail_closed":True,"dirty_entries":unrelated}
    cp=load_control_plane(control_plane_path)
    evidence=packet.get("first_meaningful_failure","")
    plan=build_repair_plan(repository=packet.get("repository",""),log_text=evidence,control_plane=cp)
    if not plan["eligible_for_auto_repair"]:
        return {"status":"REPAIR_NOT_AUTHORIZED","fail_closed":True,"plan":plan}
    result=execute_repair_plan(plan,repo_root=repo_root)
    if result["execution_status"]!="REPAIR_COMMANDS_APPLIED":
        return {"status":"REPAIR_COMMAND_FAILED","fail_closed":True,"plan":result}
    if clean(repo_root):
        return {"status":"REPAIR_NO_CHANGE","fail_closed":True,"plan":result}
    verify=run(preflight,repo_root)
    if verify.returncode:
        return {"status":"PREFLIGHT_FAILED","fail_closed":True,"plan":result,
                "preflight_stdout":verify.stdout[-4000:],"preflight_stderr":verify.stderr[-4000:]}
    if sha(repo_root)!=expected:
        return {"status":"HEAD_MOVED","fail_closed":True,"plan":result}
    return {"status":"REPAIR_VERIFIED","fail_closed":False,"plan":result,
            "expected_head_sha":expected,"changed_files":run(["git","diff","--name-only"],repo_root,True).stdout.splitlines()}

def main():
    p=argparse.ArgumentParser();p.add_argument("--packet",type=Path,required=True)
    p.add_argument("--repo-root",type=Path,required=True);p.add_argument("--control-plane",type=Path,required=True)
    p.add_argument("--preflight",nargs="+",required=True);p.add_argument("--output",type=Path)
    a=p.parse_args(); out=execute(json.loads(a.packet.read_text()),a.repo_root,a.control_plane,a.preflight)
    text=json.dumps(out,indent=2,sort_keys=True)+"\n"
    if a.output:a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(text)
    print(text,end="");return 0 if out["status"]=="REPAIR_VERIFIED" else 2
if __name__=="__main__":raise SystemExit(main())
