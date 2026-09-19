"""Conflict-aware lane leasing for concurrent autonomous slices."""
from __future__ import annotations
import json, subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

@dataclass(frozen=True)
class LaneLease:
    repo:str; lane:str; branch:str; paths:tuple[str,...]; acquired_at:str

def normalize(path:str)->str:
    return path.replace("\\","/").strip("/").lower()

def paths_conflict(left:str,right:str)->bool:
    a,b=normalize(left),normalize(right)
    if not a or not b: return True
    return a==b or a.startswith(b+"/") or b.startswith(a+"/")

def lease_conflicts(paths:list[str],leases:list[LaneLease])->bool:
    return any(paths_conflict(p,q) for lease in leases for p in paths for q in lease.paths)

def load_leases(path:Path,ttl_hours:int=6)->list[LaneLease]:
    if not path.exists(): return []
    now=datetime.now(UTC); out=[]
    try: rows=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError): return []
    for row in rows:
        try:
            acquired=datetime.fromisoformat(row["acquired_at"])
            if acquired.tzinfo is None: acquired=acquired.replace(tzinfo=UTC)
            if now-acquired <= timedelta(hours=ttl_hours):
                out.append(LaneLease(row["repo"],row["lane"],row["branch"],tuple(row["paths"]),row["acquired_at"]))
        except (KeyError,TypeError,ValueError): continue
    return out

def save_leases(path:Path,leases:list[LaneLease])->None:
    path.parent.mkdir(parents=True,exist_ok=True)
    rows=[{"repo":x.repo,"lane":x.lane,"branch":x.branch,"paths":list(x.paths),"acquired_at":x.acquired_at} for x in leases]
    tmp=path.with_suffix(".tmp"); tmp.write_text(json.dumps(rows,indent=2,sort_keys=True)+"\n",encoding="utf-8"); tmp.replace(path)

def open_pr_paths(repo:str)->list[LaneLease]:
    p=subprocess.run(["gh","pr","list","--repo",repo,"--state","open","--json","number,headRefName,files"],
                     text=True,capture_output=True,check=False)
    if p.returncode: return []
    leases=[]
    for pr in json.loads(p.stdout or "[]"):
        paths=tuple(f["path"] for f in pr.get("files",[]) if f.get("path"))
        leases.append(LaneLease(repo,f"pr-{pr['number']}",pr.get("headRefName",""),paths,datetime.now(UTC).isoformat()))
    return leases
