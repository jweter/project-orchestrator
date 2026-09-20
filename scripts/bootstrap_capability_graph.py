#!/usr/bin/env python3
"""Bootstrap conservative capability graphs from repository-declared authoritative roadmap files.

This does not infer requirements from arbitrary files. It only extracts unchecked Markdown
tasks from roadmap paths explicitly named in engineering/hands-off-authority.json.
"""
from __future__ import annotations
import argparse,json,re,subprocess
from pathlib import Path

TASK=re.compile(r"^\s*[-*]\s+\[ \]\s+(.+?)\s*$")

def head(repo:Path)->str:
    return subprocess.run(["git","rev-parse","HEAD"],cwd=repo,text=True,capture_output=True,check=True).stdout.strip()

def tracked(repo:Path,path:str)->bool:
    return subprocess.run(["git","ls-files","--error-unmatch",path],cwd=repo,capture_output=True).returncode==0

def bootstrap(repo:Path,repository:str)->dict:
    manifest=json.loads((repo/"engineering/hands-off-authority.json").read_text(encoding="utf-8"))
    roadmaps=manifest.get("authority",{}).get("roadmap",[])
    if not isinstance(roadmaps,list) or not roadmaps:
        raise RuntimeError("DECLARED_ROADMAP_REQUIRED")
    tasks=[]
    for rel in roadmaps:
        if not isinstance(rel,str) or not tracked(repo,rel):
            raise RuntimeError(f"DECLARED_ROADMAP_NOT_TRACKED:{rel}")
        for lineno,line in enumerate((repo/rel).read_text(encoding="utf-8").splitlines(),1):
            m=TASK.match(line)
            if m:
                tasks.append((rel,lineno,m.group(1).strip()))
    if not tasks:
        raise RuntimeError("NO_EXPLICIT_UNFINISHED_ROADMAP_TASKS")
    capabilities=[]
    roadmap=[]
    for n,(rel,line,obj) in enumerate(tasks,1):
        cid=f"declared-roadmap-{n:03d}"; roadmap.append(cid)
        capabilities.append({"id":cid,"capability":obj,"state":"PLANNED","depends_on":[],
            "completion_evidence":[f"{rel}:L{line} task completed and verified"],
            "slices":[{"id":f"{cid}-implementation","objective":obj,
                "acceptance":[f"Implement the explicitly declared roadmap task: {obj}",
                              "Repository preflight and required CI pass at the exact implementation head"],
                "depends_on":[],"state":"PLANNED","allowed_paths":[],"forbidden_paths":["engineering/hands-off-authority.json"]}]})
    return {"schema_version":1,"repository":repository,"source_exact_head":head(repo),
            "authority":{"manifest":"engineering/hands-off-authority.json","roadmap_files":roadmaps},
            "roadmap":roadmap,"capabilities":capabilities}

def main()->int:
    p=argparse.ArgumentParser();p.add_argument("repo",type=Path);p.add_argument("repository");p.add_argument("output",type=Path);a=p.parse_args()
    try: payload=bootstrap(a.repo,a.repository)
    except (OSError,json.JSONDecodeError,subprocess.CalledProcessError,RuntimeError) as exc:
        print(json.dumps({"status":"DIRECTION_REQUIRED","reason":str(exc)}));return 1
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(payload,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":"READY","capabilities":len(payload["capabilities"]),"output":str(a.output)}));return 0
if __name__=="__main__":raise SystemExit(main())
