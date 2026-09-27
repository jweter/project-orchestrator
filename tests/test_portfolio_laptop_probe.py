import json
from project_orchestrator.portfolio_laptop_probe import PORTFOLIO,enqueue_portfolio_probes
def test_all_six_repositories_get_production_jobs(tmp_path):
 heads={repo:(str(i)*40) for i,(_,repo) in enumerate(PORTFOLIO,1)}
 out=enqueue_portfolio_probes(heads=heads,workspace_root=str(tmp_path/"work"),pending_dir=tmp_path/"pending",claude_state_dir=tmp_path/"claude")
 assert len(out)==6;assert all(x["status"]=="QUEUED" for x in out)
 jobs=[json.loads(p.read_text()) for p in (tmp_path/"pending").glob("*.json")]
 assert {j["repository"] for j in jobs}=={repo for _,repo in PORTFOLIO}
 assert all(j["prohibitions"]["merge"] for j in jobs)
def test_missing_repo_head_fails_closed(tmp_path):
 heads={repo:"a"*40 for _,repo in PORTFOLIO[:-1]}
 try: enqueue_portfolio_probes(heads=heads,workspace_root=str(tmp_path/"w"),pending_dir=tmp_path/"p",claude_state_dir=tmp_path/"c")
 except ValueError as e: assert "VoiceStudio" in str(e)
 else: raise AssertionError("expected missing head failure")
