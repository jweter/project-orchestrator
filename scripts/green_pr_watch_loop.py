"""Lightweight always-on green PR watcher. Heavy development remains idle-gated."""
from __future__ import annotations
import argparse, json, time
from datetime import UTC, datetime
from pathlib import Path
from green_pr_merger import cycle

def atomic(path:Path,payload:dict)->None:
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    tmp.replace(path)

def main()->int:
    p=argparse.ArgumentParser()
    p.add_argument("--state",required=True)
    p.add_argument("--poll-seconds",type=int,default=60)
    p.add_argument("--once",action="store_true")
    a=p.parse_args(); state=Path(a.state)
    while True:
        started=time.time()
        try:
            results=cycle(False); error=None
        except Exception as exc:
            results=[]; error=type(exc).__name__
        atomic(state,{"updated_at_utc":datetime.now(UTC).isoformat(),"duration_seconds":round(time.time()-started,3),"error_class":error,"results":results})
        if a.once:return 0 if error is None else 1
        time.sleep(max(30,a.poll_seconds))
if __name__=="__main__": raise SystemExit(main())
