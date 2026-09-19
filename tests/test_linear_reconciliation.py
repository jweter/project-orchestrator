from src.project_orchestrator.linear_reconciliation import GithubWorkState, extract_github_refs, pr_references_issue, reconcile

def test_open_pr_promotes_to_review():
    assert reconcile(GithubWorkState(open_prs=1)).state == "In Review"

def test_active_branch_promotes_to_progress():
    assert reconcile(GithubWorkState(implementation_active=True)).state == "In Progress"

def test_merge_or_closed_issue_is_done():
    assert reconcile(GithubWorkState(merged_prs=1)).state == "Done"
    assert reconcile(GithubWorkState(issue_open=False)).state == "Done"

def test_blockers_are_managed_independently():
    d=reconcile(GithubWorkState(blocked=True,human_required=True))
    assert d.add_labels == ("Blocked","Human Required")
    assert d.remove_labels == ()

def test_phase1_reference_parser():
    desc="Imported\n\n**Authoritative source:** jweter/knowledge-engine-ai#87\n"
    assert extract_github_refs(desc) == ("jweter/knowledge-engine-ai",87)

def test_pr_issue_linkage_is_bounded():
    assert pr_references_issue({"body":"Fixes #87"},87)
    assert not pr_references_issue({"body":"Fixes #88"},87)

def test_todo_is_fail_closed_default():
    d=reconcile(GithubWorkState())
    assert d.state == "Todo"
    assert set(d.remove_labels) == {"Blocked","Human Required"}
