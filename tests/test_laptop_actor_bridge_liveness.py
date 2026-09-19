import json,sys
from pathlib import Path
from unittest.mock import patch
import scripts.laptop_actor_github_bridge as bridge

def invoke(tmp_path, cycles):
 argv=["bridge","--queue",str(tmp_path/"q"),"--workspace-root",str(tmp_path/"w"),"--runner",str(tmp_path/"runner"),"--once"]
 with patch.object(sys,"argv",argv), patch.object(bridge,"cycle",side_effect=cycles):
  code=bridge.main()
 return code,json.loads((tmp_path/"q"/"state"/"bridge-heartbeat.json").read_text())

def test_bridge_writes_healthy_heartbeat(tmp_path):
 code,hb=invoke(tmp_path,[{"github_bridge":True,"pending_remote_jobs":0,"published_results":0,"local_returncode":0}])
 assert code==0; assert hb["status"]=="HEALTHY"; assert hb["consecutive_failures"]==0

def test_bridge_failure_is_visible_not_swallowed(tmp_path):
 code,hb=invoke(tmp_path,[RuntimeError("boom")])
 assert code==1; assert hb["status"]=="DEGRADED"; assert hb["error_class"]=="RuntimeError"
