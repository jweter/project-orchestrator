from __future__ import annotations
from pathlib import Path
from typing import Any
from .execution_dispatch import execute_actor_selection
PORTFOLIO=(
 ("knowledge-engine-core","jweter/knowledge-engine-core"),
 ("knowledge-engine-web","jweter/knowledge-engine-web"),
 ("knowledge-engine-ai","jweter/knowledge-engine-ai"),
 ("rocksmith","jweter/rocksmith-cdlc-generator"),
 ("everward","jweter/Project-Everward"),
 ("voicestudio","jweter/VoiceStudio"),
)
def enqueue_portfolio_probes(*,heads:dict[str,str],workspace_root:str,pending_dir:Path,claude_state_dir:Path)->list[dict[str,Any]]:
 results=[]
 for name,repo in PORTFOLIO:
  sha=heads.get(repo)
  if not sha: raise ValueError(f"missing exact head for {repo}")
  task={"repository":repo,"task_key":f"portfolio-probe-{name}-{sha[:12]}","kind":"DETERMINISTIC_FAILURE",
   "local_capability_verified":True,"base_sha":sha,"branch":f"orchestrator/portfolio-probe-{name}",
   "verification":["git status --short","git rev-parse HEAD"],"workspace_root":workspace_root,
   "prompt":"Perform a bounded exact-head repository checkout and identity probe. Do not modify product files."}
  results.append(execute_actor_selection(task,claude_state_dir=claude_state_dir,laptop_queue_dir=pending_dir))
 return results

\n