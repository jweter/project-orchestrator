from scripts.green_pr_merger import decide

def pr(**kw):
    x={"isDraft":False,"author":{"login":"jweter"},"mergeStateStatus":"CLEAN",
       "reviewDecision":"","statusCheckRollup":[{"status":"COMPLETED","conclusion":"SUCCESS"}]}
    x.update(kw); return x

def test_green_pr_is_eligible(): assert decide(pr(),age_seconds=121).eligible
def test_draft_blocked(): assert decide(pr(isDraft=True),age_seconds=121).reason=="DRAFT"
def test_unknown_author_blocked(): assert decide(pr(author={"login":"other"}),age_seconds=121).reason=="AUTHOR_NOT_ALLOWED"
def test_review_settle_window_blocks_race(): assert decide(pr(),age_seconds=60).reason=="PR_AGE_WINDOW"
def test_conflict_blocked(): assert not decide(pr(mergeStateStatus="DIRTY"),age_seconds=121).eligible
def test_missing_checks_fail_closed(): assert decide(pr(statusCheckRollup=[]),age_seconds=121).reason=="CHECKS_MISSING"
def test_pending_checks_fail_closed():
    checks=[{"status":"IN_PROGRESS","conclusion":""}]
    assert decide(pr(statusCheckRollup=checks),age_seconds=121).reason=="CHECKS_PENDING"
def test_failed_check_blocked():
    checks=[{"status":"COMPLETED","conclusion":"FAILURE"}]
    assert decide(pr(statusCheckRollup=checks),age_seconds=121).reason=="CHECK_FAILURE"
def test_changes_requested_blocked():
    assert decide(pr(reviewDecision="CHANGES_REQUESTED"),age_seconds=121).reason=="REVIEW_CHANGES_REQUESTED"
def test_current_blocking_review_finding_blocks_merge():
    assert decide(pr(),blocking_findings=True,age_seconds=121).reason=="BLOCKING_REVIEW_FINDING"
def test_neutral_and_skipped_checks_allowed():
    checks=[{"status":"COMPLETED","conclusion":"NEUTRAL"},{"status":"COMPLETED","conclusion":"SKIPPED"}]
    assert decide(pr(statusCheckRollup=checks),age_seconds=121).eligible

def test_green_draft_can_be_promoted():
    assert decide(pr(isDraft=True),age_seconds=121,allow_draft=True).eligible

def test_green_draft_promotion_still_respects_review_gate():
    d=decide(pr(isDraft=True,reviewDecision="CHANGES_REQUESTED"),age_seconds=121,allow_draft=True)
    assert d.reason=="REVIEW_CHANGES_REQUESTED"

def test_green_draft_promotion_still_requires_checks():
    d=decide(pr(isDraft=True,statusCheckRollup=[]),age_seconds=121,allow_draft=True)
    assert d.reason=="CHECKS_MISSING"

def test_draft_requires_refetch_before_merge(monkeypatch):
    import scripts.green_pr_merger as merger
    draft=pr(isDraft=True,number=7,headRefOid="abc",createdAt="2020-01-01T00:00:00Z")
    monkeypatch.setattr(merger,"PORTFOLIO_REPOS",("owner/repo",))
    monkeypatch.setattr(merger,"open_prs",lambda repo:[draft])
    monkeypatch.setattr(merger,"blocking_review_findings",lambda *args:False)
    monkeypatch.setattr(merger,"review_settled",lambda *args:True)
    monkeypatch.setattr(merger.subprocess,"run",lambda *args,**kwargs:type("P",(),{"returncode":0})())
    refreshed=pr(isDraft=False,number=7,headRefOid="abc",createdAt="2020-01-01T00:00:00Z")
    monkeypatch.setattr(merger,"gh_json",lambda args:refreshed)
    seen=[]
    monkeypatch.setattr(merger,"merge",lambda repo,p,dry_run=False:seen.append(p) or {"merged":True})
    results=merger.cycle(False)
    assert seen==[refreshed]
    assert results[-1]["merged"] is True


def test_review_usage_limit_is_blocking_evidence(monkeypatch):
    import scripts.green_pr_merger as merger
    monkeypatch.setattr(merger,"gh_json",lambda args:[{"commit_id":"head","body":"You have reached your Codex usage limits for code reviews."}] if args[-1].endswith("/comments") else [])
    assert merger.blocking_review_findings("o/r",1,"head") is True


def test_repository_failure_does_not_block_other_repositories(monkeypatch):
    import scripts.green_pr_merger as merger
    good=pr(number=8,headRefOid="good",createdAt="2020-01-01T00:00:00Z")
    monkeypatch.setattr(merger,"PORTFOLIO_REPOS",("broken/repo","good/repo"))
    def fake_open(repo):
        if repo=="broken/repo": raise RuntimeError("transient GitHub failure")
        return [good]
    monkeypatch.setattr(merger,"open_prs",fake_open)
    monkeypatch.setattr(merger,"merge",lambda repo,p,dry_run=False:{"repository":repo,"pr":p["number"],"merged":True})
    results=merger.cycle(False)
    assert results[0]["decision"]=="REPOSITORY_QUERY_FAILED"
    assert results[1]["repository"]=="good/repo"
    assert results[1]["merged"] is True


def test_pr_failure_does_not_block_next_pr(monkeypatch):
    import scripts.green_pr_merger as merger
    bad=pr(number=1,headRefOid="bad",createdAt="2020-01-01T00:00:00Z")
    good=pr(number=2,headRefOid="good",createdAt="2020-01-01T00:00:00Z")
    monkeypatch.setattr(merger,"PORTFOLIO_REPOS",("owner/repo",))
    monkeypatch.setattr(merger,"open_prs",lambda repo:[bad,good])
    def fake_merge(repo,p,dry_run=False):
        if p["number"]==1: raise RuntimeError("one PR failed")
        return {"repository":repo,"pr":p["number"],"merged":True}
    monkeypatch.setattr(merger,"merge",fake_merge)
    results=merger.cycle(False)
    assert results[0]["decision"]=="PR_PROCESSING_FAILED"
    assert results[0]["pr"]==1
    assert results[1]["pr"]==2
    assert results[1]["merged"] is True
