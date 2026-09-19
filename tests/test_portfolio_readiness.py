from project_orchestrator.portfolio_readiness import assess_portfolio_readiness
from project_orchestrator.portfolio_laptop_probe import PORTFOLIO
def rows(now=1000):
 return [{"repository":repo,"status":"PASS","completed_at":now} for _,repo in PORTFOLIO]
def test_all_five_recent_passes_are_ready():
 r=assess_portfolio_readiness(rows(),now=1000);assert r["healthy"];assert len(r["covered"])==5
def test_one_missing_repo_fails_gate():
 r=assess_portfolio_readiness(rows()[:-1],now=1000);assert not r["healthy"];assert r["missing"]==["jweter/Project-Everward"]
def test_failure_or_stale_repo_fails_gate():
 x=rows();x[1]["status"]="FAIL";x[2]["completed_at"]=0
 r=assess_portfolio_readiness(x,max_age_seconds=100,now=1000);assert not r["healthy"];assert "jweter/knowledge-engine-web" in r["failed"];assert "jweter/knowledge-engine-ai" in r["stale"]
