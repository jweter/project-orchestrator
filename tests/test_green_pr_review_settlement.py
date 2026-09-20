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
    monkeypatch.setattr(merger,"gh_json",fake)
    assert merger.review_settled("o/r",1,"head",created) is False

def test_old_exact_head_review_allows_settlement(monkeypatch):
    created=_iso(900)
    def fake(args):
        if args[-1].endswith("/comments"):
            return [{"commit_id":"head","updated_at":_iso(600),"body":"done"}]
        if args[-1].endswith("/reviews"):
            return [{"commit_id":"head","submitted_at":_iso(550)}]
        raise AssertionError(args)
    monkeypatch.setattr(merger,"gh_json",fake)
    assert merger.review_settled("o/r",1,"head",created) is True

def test_stale_head_comment_does_not_delay_current_head(monkeypatch):
    created=_iso(600)
    def fake(args):
        if args[-1].endswith("/comments"):
            return [{"commit_id":"old","updated_at":_iso(10),"body":"late old-head finding"}]
        if args[-1].endswith("/reviews"):
            return []
        raise AssertionError(args)
    monkeypatch.setattr(merger,"gh_json",fake)
    assert merger.review_settled("o/r",1,"head",created) is True

def test_decision_rejects_unsettled_review_even_when_ci_green():
    pr={"isDraft":False,"author":{"login":"jweter"},"mergeStateStatus":"CLEAN",
        "reviewDecision":"","statusCheckRollup":[{"status":"COMPLETED","conclusion":"SUCCESS"}]}
    decision=merger.decide(pr,age_seconds=600,review_settled=False)
    assert not decision.eligible
    assert decision.reason=="REVIEW_SETTLE_WINDOW"
