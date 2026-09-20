from datetime import UTC, datetime, timedelta
import scripts.green_pr_merger as merger

def _iso(seconds_ago):
    return (datetime.now(UTC)-timedelta(seconds=seconds_ago)).isoformat().replace("+00:00","Z")

def test_exact_head_recent_review_keeps_merge_in_settlement_window(monkeypatch):
    created=_iso(600)
    def fake(args):
        if args[-1].endswith("/comments"):
            return [{"commit_id":"head","updated_at":_iso(60),"body":"reviewing"}]
        if args[-1].endswith("/reviews"):
            return []
        raise AssertionError(args)
    monkeypatch.setattr(merger,"gh_json",lambda args:{"commit":{"committer":{"date":created}}})
    monkeypatch.setattr(merger,"gh_paginated",lambda endpoint: fake(["api",endpoint]))
    assert merger.review_settled("o/r",1,"head",created) is False

def test_old_exact_head_review_allows_settlement(monkeypatch):
    created=_iso(900)
    def fake(args):
        if args[-1].endswith("/comments"):
            return [{"commit_id":"head","updated_at":_iso(600),"body":"done"}]
        if args[-1].endswith("/reviews"):
            return [{"commit_id":"head","submitted_at":_iso(550)}]
        raise AssertionError(args)
    monkeypatch.setattr(merger,"gh_json",lambda args:{"commit":{"committer":{"date":created}}})
    monkeypatch.setattr(merger,"gh_paginated",lambda endpoint: fake(["api",endpoint]))
    assert merger.review_settled("o/r",1,"head",created) is True

def test_stale_head_comment_does_not_delay_current_head(monkeypatch):
    created=_iso(600)
    def fake(args):
        if args[-1].endswith("/comments"):
            return [{"commit_id":"old","updated_at":_iso(10),"body":"late old-head finding"}]
        if args[-1].endswith("/reviews"):
            return []
        raise AssertionError(args)
    monkeypatch.setattr(merger,"gh_json",lambda args:{"commit":{"committer":{"date":created}}})
    monkeypatch.setattr(merger,"gh_paginated",lambda endpoint: fake(["api",endpoint]))
    assert merger.review_settled("o/r",1,"head",created) is True

def test_decision_rejects_unsettled_review_even_when_ci_green():
    pr={"isDraft":False,"author":{"login":"jweter"},"mergeStateStatus":"CLEAN",
        "reviewDecision":"","statusCheckRollup":[{"status":"COMPLETED","conclusion":"SUCCESS"}]}
    decision=merger.decide(pr,age_seconds=600,review_settled=False)
    assert not decision.eligible
    assert decision.reason=="REVIEW_SETTLE_WINDOW"


def test_new_head_restarts_settlement_clock_on_old_pr(monkeypatch):
    created=_iso(86400)
    head_time=_iso(30)
    monkeypatch.setattr(merger,"gh_json",lambda args:{"commit":{"committer":{"date":head_time}}})
    monkeypatch.setattr(merger,"gh_paginated",lambda endpoint:[])
    assert merger.review_settled("o/r",1,"new-head",created) is False

def test_paginated_review_activity_uses_late_page(monkeypatch):
    created=_iso(900)
    head_time=_iso(800)
    monkeypatch.setattr(merger,"gh_json",lambda args:{"commit":{"committer":{"date":head_time}}})
    def pages(endpoint):
        if endpoint.endswith("/comments"):
            return [{"commit_id":"head","updated_at":_iso(700)},{"commit_id":"head","updated_at":_iso(20)}]
        return []
    monkeypatch.setattr(merger,"gh_paginated",pages)
    assert merger.review_settled("o/r",1,"head",created) is False
