from scripts.green_pr_merger import decide

def pr(**kw):
    x={"isDraft":False,"author":{"login":"jweter"},"mergeStateStatus":"CLEAN",
       "reviewDecision":"","statusCheckRollup":[{"status":"COMPLETED","conclusion":"SUCCESS"}]}
    x.update(kw); return x

def test_green_pr_is_eligible(): assert decide(pr(),age_seconds=121).eligible
def test_draft_blocked(): assert decide(pr(isDraft=True),age_seconds=121).reason=="DRAFT"
def test_unknown_author_blocked(): assert decide(pr(author={"login":"other"}),age_seconds=121).reason=="AUTHOR_NOT_ALLOWED"
def test_review_settle_window_blocks_race(): assert decide(pr(),age_seconds=60).reason=="REVIEW_SETTLE_WINDOW"
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
