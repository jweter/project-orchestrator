from scripts.green_pr_merger import decide

def pr(**kw):
    x={"isDraft":False,"author":{"login":"jweter"},"mergeStateStatus":"CLEAN",
       "reviewDecision":"","statusCheckRollup":[{"status":"COMPLETED","conclusion":"SUCCESS"}]}
    x.update(kw); return x

def test_green_pr_is_eligible(): assert decide(pr(),age_seconds=121).eligible
def test_draft_blocked(): assert decide(pr(isDraft=True),age_seconds=121).reason=="DRAFT"
def test_unknown_author_blocked(): assert decide(pr(author={"login":"other"}),age_seconds=121).reason=="AUTHOR_NOT_ALLOWED"
def test_dependabot_app_author_is_allowed(): assert decide(pr(author={"login":"app/dependabot"}),age_seconds=121).reason!="AUTHOR_NOT_ALLOWED"
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
    payload={"data":{"repository":{"pullRequest":{"reviewThreads":{"pageInfo":{"hasNextPage":False},"nodes":[{"isResolved":False,"comments":{"pageInfo":{"hasNextPage":False},"nodes":[{"body":"You have reached your Codex usage limits for code reviews.","commit":{"oid":"head"}}]}}]}}}}}
    monkeypatch.setattr(merger,"gh_json",lambda args:payload)
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


def test_resolved_blocking_review_thread_does_not_block(monkeypatch):
    import scripts.green_pr_merger as merger
    payload={"data":{"repository":{"pullRequest":{"reviewThreads":{"pageInfo":{"hasNextPage":False},"nodes":[{"isResolved":True,"comments":{"pageInfo":{"hasNextPage":False},"nodes":[{"body":"P1 blocking regression","commit":{"oid":"head"}}]}}]}}}}}
    monkeypatch.setattr(merger,"gh_json",lambda args:payload)
    assert merger.blocking_review_findings("o/r",1,"head") is False


def test_unresolved_blocking_review_thread_blocks(monkeypatch):
    import scripts.green_pr_merger as merger
    payload={"data":{"repository":{"pullRequest":{"reviewThreads":{"pageInfo":{"hasNextPage":False},"nodes":[{"isResolved":False,"comments":{"pageInfo":{"hasNextPage":False},"nodes":[{"body":"P1 blocking regression","commit":{"oid":"head"}}]}}]}}}}}
    monkeypatch.setattr(merger,"gh_json",lambda args:payload)
    assert merger.blocking_review_findings("o/r",1,"head") is True


def test_review_thread_resolution_unavailable_fails_closed(monkeypatch):
    import pytest
    import scripts.green_pr_merger as merger
    monkeypatch.setattr(merger,"gh_json",lambda args:{"data":{"repository":{"pullRequest":{}}}})
    with pytest.raises(RuntimeError,match="resolution state unavailable"):
        merger.blocking_review_findings("o/r",1,"head")


def test_review_reader_refuses_incomplete_graphql_pages(monkeypatch):
    """A GitHub GraphQL first:100 cap must not hide a blocking review."""
    import pytest
    import scripts.green_pr_merger as merger

    thread={"isResolved":False,"path":"src/safety.py","comments":{
        "nodes":[{"body":"P1 authorization regression","commit":{"oid":"head"}}]}}
    def check(review_nodes, *, review_more=False, comment_more=False):
        review={"nodes":review_nodes,"pageInfo":{"hasNextPage":review_more}}
        if comment_more:
            review_nodes[0]["comments"]["pageInfo"]={"hasNextPage":True}
        monkeypatch.setattr(merger,"gh_json",lambda _args:{
            "data":{"repository":{"pullRequest":{"reviewThreads":review}}}})
        with pytest.raises(RuntimeError,match="REVIEW_(THREADS|COMMENTS)_INCOMPLETE"):
            merger.review_threads("o/r",1)

    check([thread.copy()],review_more=True)
    check([thread.copy() for _ in range(100)])
    check([thread.copy()],comment_more=True)
    large={"isResolved":False,"comments":{"pageInfo":{"hasNextPage":False},"nodes":[{"body":"P1","commit":{"oid":"head"}}] * 100}}
    check([large])


def test_review_reader_rejects_malformed_comment_payload(monkeypatch):
    import pytest
    import scripts.green_pr_merger as merger

    monkeypatch.setattr(merger,"gh_json",lambda _args:{
        "data":{"repository":{"pullRequest":{"reviewThreads":{"pageInfo":{"hasNextPage":False},"nodes":[
            {"isResolved":False,"comments":{}}]}}}}})
    with pytest.raises(RuntimeError,match="REVIEW_COMMENTS_INCOMPLETE"):
        merger.review_threads("o/r",1)


def test_review_reader_rejects_missing_or_invalid_pagination_metadata(monkeypatch):
    """Missing pageInfo is not proof that all review findings were delivered."""
    import pytest
    import scripts.green_pr_merger as merger

    comment={"body":"P1 must not merge", "commit":{"oid":"current-head"}}
    payloads=[
        {"nodes":[]},
        {"pageInfo":{},"nodes":[]},
        {"pageInfo":{"hasNextPage":None},"nodes":[]},
        {"pageInfo":{"hasNextPage":"false"},"nodes":[]},
        {"pageInfo":{"hasNextPage":False},"nodes":[
            {"isResolved":False,"comments":{"nodes":[comment]}}]},
        {"pageInfo":{"hasNextPage":False},"nodes":[
            {"isResolved":False,"comments":{"pageInfo":{},"nodes":[comment]}}]},
        {"pageInfo":{"hasNextPage":False},"nodes":[
            {"isResolved":False,"comments":{"pageInfo":{"hasNextPage":"false"},
                                             "nodes":[comment]}}]},
    ]
    for review_data in payloads:
        monkeypatch.setattr(merger,"gh_json",lambda _args,data=review_data:{
            "data":{"repository":{"pullRequest":{"reviewThreads":data}}}})
        with pytest.raises(RuntimeError,match="REVIEW_(THREADS|COMMENTS)_INCOMPLETE"):
            merger.review_threads("o/r",1)

    # A genuinely complete empty review page remains valid (not a missing response).
    monkeypatch.setattr(merger,"gh_json",lambda _args:{
        "data":{"repository":{"pullRequest":{"reviewThreads":{
            "pageInfo":{"hasNextPage":False},"nodes":[]}}}}})
    assert merger.review_threads("o/r",1)==[]

def test_independent_exact_head_review_requires_clean_codex_receipt_and_resolved_sha(monkeypatch):
    import scripts.green_pr_merger as merger

    head = "a" * 40
    body = (
        "<!-- codex-review-receipt:v1 -->\n"
        "Codex Review: Didn't find any major issues.\n"
        "**Reviewed commit:** `aaaaaaaaaa`"
    )
    monkeypatch.setattr(merger, "gh_paginated", lambda _endpoint: [
        {"user": {"login": "chatgpt-codex-connector[bot]"}, "body": body,
         "created_at": "2026-11-16T14:00:00Z", "updated_at": "2026-11-16T14:00:00Z"}
    ])
    monkeypatch.setattr(merger, "gh_json", lambda _args: {"sha": head})
    assert merger.independent_exact_head_review("o/r", 1, head, "jweter", []) is True
    monkeypatch.setattr(merger, "gh_json", lambda _args: {"sha": "b" * 40})
    assert merger.independent_exact_head_review("o/r", 1, head, "jweter", []) is False


def test_stale_or_spoofed_codex_receipts_do_not_unlock_merge(monkeypatch):
    import scripts.green_pr_merger as merger

    head = "a" * 40
    body = (
        "Codex Review: Didn't find any major issues.\n"
        "**Reviewed commit:** `bbbbbbbbbb`"
    )
    comments = [{"user": {"login": "chatgpt-codex-connector[bot]"}, "body": body}]
    monkeypatch.setattr(merger, "gh_paginated", lambda _endpoint: comments)
    monkeypatch.setattr(merger, "gh_json", lambda _args: {"sha": "b" * 40})
    assert not merger.independent_exact_head_review("o/r", 1, head, "jweter", [])
    comments[0] = {
        "user": {"login": "jweter"},
        "body": body.replace("bbbbbbbbbb", "aaaaaaaaaa"),
    }
    assert not merger.independent_exact_head_review("o/r", 1, head, "jweter", [])


def test_only_independent_current_head_approval_counts(monkeypatch):
    import scripts.green_pr_merger as merger

    head = "c" * 40
    monkeypatch.setattr(merger, "gh_paginated", lambda _endpoint: [])
    accepted = [{"user": {"login": "reviewer"}, "commit_id": head, "state": "APPROVED",
                 "author_association": "COLLABORATOR", "submitted_at": "2026-11-16T13:00:00Z"}]
    assert merger.independent_exact_head_review("o/r", 1, head, "jweter", accepted)
    accepted[0]["author_association"] = "CONTRIBUTOR"
    assert not merger.independent_exact_head_review("o/r", 1, head, "jweter", accepted)
    accepted[0]["author_association"] = "COLLABORATOR"
    assert not merger.independent_exact_head_review("o/r", 1, head, "reviewer", accepted)
    accepted[0]["commit_id"] = "d" * 40
    assert not merger.independent_exact_head_review("o/r", 1, head, "jweter", accepted)
    accepted[0]["commit_id"] = head
    accepted[0]["state"] = "COMMENTED"
    assert not merger.independent_exact_head_review("o/r", 1, head, "jweter", accepted)


def test_quiet_period_without_independent_current_head_review_still_blocks(monkeypatch):
    import scripts.green_pr_merger as merger

    head = "a" * 40
    def gh_fake(args):
        if args[1].startswith("repos/o/r/commits/"):
            return {"commit": {"committer": {"date": "2020-01-01T00:00:00Z"}}}
        return {"user": {"login": "jweter"}}
    monkeypatch.setattr(merger, "gh_json", gh_fake)
    monkeypatch.setattr(merger, "gh_paginated", lambda _endpoint: [])
    assert not merger.review_settled("o/r", 1, head, "2020-01-01T00:00:00Z")


def test_clean_receipt_edited_by_repository_writer_is_not_independent_evidence(monkeypatch):
    import scripts.green_pr_merger as merger

    head = "a" * 40
    comment = {
        "user": {"login": "chatgpt-codex-connector[bot]"},
        "body": "Codex Review: Didn't find any major issues.\n**Reviewed commit:** `aaaaaaaaaa`",
        "created_at": "2026-11-16T14:00:00Z",
        "updated_at": "2026-11-16T14:00:02Z",
    }
    monkeypatch.setattr(merger, "gh_paginated", lambda _endpoint: [comment])
    monkeypatch.setattr(merger, "gh_json", lambda _args: {"sha": head})
    assert not merger.independent_exact_head_review("o/r", 1, head, "jweter", [])


def test_later_changes_requested_revokes_an_earlier_exact_head_approval(monkeypatch):
    import scripts.green_pr_merger as merger

    head = "a" * 40
    monkeypatch.setattr(merger, "gh_paginated", lambda _endpoint: [])
    who = {"login": "trusted-reviewer"}
    earlier = {"user": who, "author_association": "COLLABORATOR", "commit_id": head,
               "state": "APPROVED", "submitted_at": "2026-11-16T13:00:00Z"}
    later = {**earlier, "state": "CHANGES_REQUESTED", "submitted_at": "2026-11-16T14:00:00Z"}
    assert not merger.independent_exact_head_review("o/r", 1, head, "jweter", [earlier, later])
    assert not merger.independent_exact_head_review("o/r", 1, head, "jweter", [later, earlier])


def test_recent_valid_codex_receipt_restarts_merge_quiet_period(monkeypatch):
    import scripts.green_pr_merger as merger

    head = "a" * 40
    created = (merger.datetime.now(merger.UTC) - __import__("datetime").timedelta(seconds=900)).isoformat()
    recent = (merger.datetime.now(merger.UTC) - __import__("datetime").timedelta(seconds=10)).isoformat()
    def gh_fake(args):
        if "/commits/" in args[-1]:
            return {"sha": head, "commit": {"committer": {"date": created}}}
        return {"user": {"login": "jweter"}}
    def paged(endpoint):
        if "issues/1/comments" in endpoint:
            return [{"user": {"login": "chatgpt-codex-connector[bot]"},
                     "body": "Codex Review: Didn't find any major issues.\n**Reviewed commit:** `aaaaaaaaaa`",
                     "created_at": recent, "updated_at": recent}]
        return []
    monkeypatch.setattr(merger, "gh_json", gh_fake)
    monkeypatch.setattr(merger, "gh_paginated", paged)
    assert merger.review_settled("o/r", 1, head, created) is False


def test_comment_only_review_does_not_clear_active_changes_request(monkeypatch):
    import scripts.green_pr_merger as merger

    head = "a" * 40
    monkeypatch.setattr(merger, "gh_paginated", lambda _endpoint: [])
    requesting = {
        "user": {"login": "reviewer1"}, "commit_id": head,
        "author_association": "COLLABORATOR", "state": "CHANGES_REQUESTED",
        "submitted_at": "2026-11-16T13:00:00Z",
    }
    commented = {**requesting, "state": "COMMENTED",
                 "submitted_at": "2026-11-16T14:00:00Z"}
    other_approval = {
        "user": {"login": "reviewer2"}, "commit_id": head,
        "author_association": "MEMBER", "state": "APPROVED",
        "submitted_at": "2026-11-16T14:10:00Z",
    }
    assert not merger.independent_exact_head_review(
        "o/r", 1, head, "jweter", [requesting, commented, other_approval]
    )
    assert not merger.independent_exact_head_review(
        "o/r", 1, head, "jweter", [other_approval, commented, requesting]
    )

    # Only an explicit new decision can supersede the active rejection.
    approved_later = {**requesting, "state": "APPROVED",
                      "submitted_at": "2026-11-16T14:20:00Z"}
    assert merger.independent_exact_head_review(
        "o/r", 1, head, "jweter", [requesting, commented, approved_later]
    )
    assert merger.independent_exact_head_review(
        "o/r", 1, head, "jweter", [approved_later, commented, requesting]
    )


def test_comment_only_review_preserves_a_prior_approval(monkeypatch):
    import scripts.green_pr_merger as merger

    head = "a" * 40
    monkeypatch.setattr(merger, "gh_paginated", lambda _endpoint: [])
    approved = {
        "user": {"login": "reviewer"}, "commit_id": head,
        "author_association": "COLLABORATOR", "state": "APPROVED",
        "submitted_at": "2026-11-16T13:00:00Z",
    }
    commented = {**approved, "state": "COMMENTED",
                 "submitted_at": "2026-11-16T14:00:00Z"}
    assert merger.independent_exact_head_review(
        "o/r", 1, head, "jweter", [approved, commented]
    )
    dismissed = {**approved, "state": "DISMISSED",
                 "submitted_at": "2026-11-16T14:20:00Z"}
    assert not merger.independent_exact_head_review(
        "o/r", 1, head, "jweter", [approved, commented, dismissed]
    )


def test_old_head_changes_request_blocks_clean_current_head_codex_receipt(monkeypatch):
    import scripts.green_pr_merger as merger

    head = "a" * 40
    old_head = "b" * 40
    bot = {
        "user": {"login": "chatgpt-codex-connector[bot]"},
        "body": "Codex Review: Didn't find any major issues.\n"
        "**Reviewed commit:** `aaaaaaaaaa`",
        "created_at": "2026-11-16T14:00:00Z",
        "updated_at": "2026-11-16T14:00:00Z",
    }
    monkeypatch.setattr(merger, "gh_paginated", lambda _endpoint: [bot])
    monkeypatch.setattr(merger, "gh_json", lambda _args: {"sha": head})
    requested = {
        "user": {"login": "collaborator"}, "author_association": "COLLABORATOR",
        "commit_id": old_head, "state": "CHANGES_REQUESTED",
        "submitted_at": "2026-11-16T13:00:00Z",
    }
    new_head_comment = {
        **requested, "commit_id": head, "state": "COMMENTED",
        "submitted_at": "2026-11-16T13:40:00Z",
    }
    assert not merger.independent_exact_head_review(
        "o/r", 1, head, "jweter", [requested, new_head_comment]
    )
    explicit_approval = {
        **requested, "commit_id": head, "state": "APPROVED",
        "submitted_at": "2026-11-16T14:20:00Z",
    }
    assert merger.independent_exact_head_review(
        "o/r", 1, head, "jweter",
        [new_head_comment, explicit_approval, requested],
    )


def test_prior_head_approval_does_not_satisfy_exact_head_gate(monkeypatch):
    import scripts.green_pr_merger as merger

    monkeypatch.setattr(merger, "gh_paginated", lambda _endpoint: [])
    old_approval = {
        "user": {"login": "reviewer"}, "author_association": "COLLABORATOR",
        "commit_id": "b" * 40, "state": "APPROVED",
        "submitted_at": "2026-11-16T13:00:00Z",
    }
    newer_comment = {
        **old_approval, "commit_id": "a" * 40, "state": "COMMENTED",
        "submitted_at": "2026-11-16T14:00:00Z",
    }
    assert not merger.independent_exact_head_review(
        "o/r", 1, "a" * 40, "jweter", [old_approval, newer_comment]
    )
