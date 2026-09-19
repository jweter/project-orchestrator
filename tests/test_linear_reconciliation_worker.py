import scripts.linear_reconciliation_worker as w
from src.project_orchestrator.linear_reconciliation import GithubWorkState

def test_desired_mutation_promotes_open_pr(monkeypatch):
    monkeypatch.setattr(w,"github_state",lambda repo,n:GithubWorkState(open_prs=1))
    rows=[{"linear_id":"JER-1","linear_state":"Todo","repo":"jweter/knowledge-engine-core","issue_number":7}]
    got=w.desired_mutations(rows)
    assert got[0]["state"]=="In Review"
    assert got[0]["source"]=="jweter/knowledge-engine-core#7"

def test_unknown_repo_is_fail_closed(monkeypatch):
    monkeypatch.setattr(w,"github_state",lambda repo,n:(_ for _ in ()).throw(AssertionError()))
    rows=[{"linear_id":"JER-1","linear_state":"Todo","repo":"evil/repo","issue_number":7}]
    assert w.desired_mutations(rows)==[]
