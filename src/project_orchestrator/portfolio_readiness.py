from __future__ import annotations
import json,time
from pathlib import Path
from typing import Any
from .portfolio_laptop_probe import PORTFOLIO
EXPECTED={repo for _,repo in PORTFOLIO}
def assess_portfolio_readiness(results:list[dict[str,Any]],*,max_age_seconds:int=86400,now:float|None=None)->dict[str,Any]:
 now=time.time() if now is None else now;by_repo={}
 for r in results:
  repo=r.get("repository")
  if repo in EXPECTED: by_repo[repo]=r
 missing=sorted(EXPECTED-set(by_repo));failed=[];stale=[]
 for repo,r in by_repo.items():
  if str(r.get("status","")).upper() not in {"PASS","NO_OP"}: failed.append(repo)
  ts=r.get("completed_at")
  if not isinstance(ts,(int,float)) or now-ts>max_age_seconds: stale.append(repo)
 healthy=not missing and not failed and not stale
 return {"status":"PORTFOLIO_READY" if healthy else "PORTFOLIO_NOT_READY","healthy":healthy,
  "covered":sorted(by_repo),"missing":missing,"failed":sorted(failed),"stale":sorted(stale)}
def persist_readiness(report:dict[str,Any],path:Path)->None:
 path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(".tmp")
 tmp.write_text(json.dumps(report,sort_keys=True,indent=2)+chr(10),encoding="utf-8");tmp.replace(path)
