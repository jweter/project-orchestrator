"""Transport-neutral worker for GitHub -> Linear reconciliation.

The worker emits desired mutations. A connector-capable caller applies them; no
Linear credentials are stored on the laptop.
"""
from __future__ import annotations
import argparse, json, subprocess, sys
from pathlib import Path

REPO_ROOT=Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0,str(REPO_ROOT))
from src.project_orchestrator.linear_reconciliation import GithubWorkState, reconcile

REPOS=(
 "jweter/knowledge-engine-core","jweter/knowledge-engine-web",
 "jweter/knowledge-engine-ai","jweter/rocksmith-cdlc-generator",
 "jweter/Project-Everward","jweter/VoiceStudio","jweter/project-orchestrator",
)

def gh_json(args:list[str])->object:
    p=subprocess.run(["gh",*args],text=True,capture_output=True,check=False,
        encoding="utf-8",errors="replace")
    if p.returncode: raise RuntimeError(p.stderr.strip() or "gh failed")
    return json.loads(p.stdout or "null")

def github_state(repo:str,number:int)->GithubWorkState:
    issue=gh_json(["issue","view",str(number),"--repo",repo,"--json","state"])
    prs=gh_json(["pr","list","--repo",repo,"--state","all","--limit","30",
        "--search",f"#{number}","--json","number,title,body,state,mergedAt"]) or []
    needle=f"#{number}"
    linked=[p for p in prs if needle in ((p.get("title") or "")+"\n"+(p.get("body") or ""))]
    return GithubWorkState(issue_open=issue.get("state")=="OPEN",
        open_prs=sum(p.get("state")=="OPEN" for p in linked),
        merged_prs=sum(bool(p.get("mergedAt")) for p in linked))

def desired_mutations(bindings:list[dict])->list[dict]:
    changes=[]
    for b in bindings:
        repo=b["repo"]; number=int(b["issue_number"])
        if repo not in REPOS: continue
        d=reconcile(github_state(repo,number))
        if d.state != b.get("linear_state") or d.add_labels or d.remove_labels:
            changes.append({"linear_id":b["linear_id"],"state":d.state,
                "add_labels":list(d.add_labels),"remove_labels":list(d.remove_labels),
                "source":f"{repo}#{number}"})
    return changes

def main()->int:
    p=argparse.ArgumentParser(); p.add_argument("--bindings",required=True)
    p.add_argument("--output",required=True); a=p.parse_args()
    bindings=json.loads(Path(a.bindings).read_text(encoding="utf-8"))
    result={"schema_version":1,"mutations":desired_mutations(bindings)}
    path=Path(a.output); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    return 0
if __name__=="__main__": raise SystemExit(main())
