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


def test_queue_refill_selects_one_non_reality_candidate_per_empty_product():
    from src.project_orchestrator.linear_reconciliation import choose_queue_refills
    rows=[
      {"linear_id":"JER-1","project":"A","status":"Done","labels":[]},
      {"linear_id":"JER-2","project":"A","status":"Backlog","labels":[],"priority_rank":2},
      {"linear_id":"JER-3","project":"A","status":"Backlog","labels":["Product Reality"],"priority_rank":1},
      {"linear_id":"JER-4","project":"B","status":"Todo","labels":[]},
      {"linear_id":"JER-5","project":"B","status":"Backlog","labels":[]},
    ]
    assert choose_queue_refills(rows)==["JER-2"]

def test_control_plane_does_not_count_as_product_refill():
    from src.project_orchestrator.linear_reconciliation import choose_queue_refills
    rows=[{"linear_id":"JER-9","project":"Exponential Engine / Project Orchestrator","status":"Backlog","labels":[]}]
    assert choose_queue_refills(rows)==[]
