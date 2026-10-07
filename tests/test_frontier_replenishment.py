import json

from project_orchestrator.frontier_replenishment import replenish_frontiers, reconcile_issue_frontier, reconcile_issue_cycle


def test_continuation_request_replenishes_from_compiled_authority(tmp_path):
    (tmp_path / "continuation-request.json").write_text(json.dumps({
        "repository": "o/r", "exact_head": "old", "reason": "REFRESH_AUTHORIZED_CANDIDATES"
    }))

    def compile_fn(_workspace, _state):
        return {"repositories": {"o/r": {"status": "COMPILED", "exact_head": "new"}}}

    def load_fn(_state, repo, head):
        assert (repo, head) == ("o/r", "new")
        return {"slice_id": "next", "capability_id": "cap-next"}

    result = replenish_frontiers(tmp_path / "ws", tmp_path, compile_fn=compile_fn, load_slice_fn=load_fn)
    assert result["repositories"]["o/r"]["status"] == "REPLENISHED"
    assert result["repositories"]["o/r"]["slice_id"] == "next"
    assert not (tmp_path / "continuation-request.json").exists()


def test_direction_request_can_self_heal_when_current_authority_now_has_slice(tmp_path):
    root = tmp_path / "direction-requests"; root.mkdir()
    path = root / "o__r.json"
    path.write_text(json.dumps({"status": "DIRECTION_REQUIRED", "repository": "o/r", "exact_head": "old"}))

    def compile_fn(_workspace, _state):
        return {"repositories": {"o/r": {"status": "COMPILED", "exact_head": "new"}}}

    result = replenish_frontiers(
        tmp_path / "ws", tmp_path, compile_fn=compile_fn,
        load_slice_fn=lambda *_: {"slice_id": "s2", "capability_id": "c2"},
    )
    assert result["repositories"]["o/r"]["status"] == "REPLENISHED"
    assert not path.exists()


def test_missing_or_insufficient_authority_stays_direction_required(tmp_path):
    (tmp_path / "continuation-request.json").write_text(json.dumps({
        "repository": "o/r", "exact_head": "head"
    }))
    result = replenish_frontiers(
        tmp_path / "ws", tmp_path,
        compile_fn=lambda *_: {"repositories": {"o/r": {"status": "DIRECTION_REQUIRED", "reason": "NO_DECLARATION"}}},
        load_slice_fn=lambda *_: (_ for _ in ()).throw(AssertionError("must not derive without authority")),
    )
    assert result["repositories"]["o/r"]["status"] == "DIRECTION_REQUIRED"
    assert (tmp_path / "continuation-request.json").exists()


def test_no_requests_does_not_compile(tmp_path):
    result = replenish_frontiers(
        tmp_path / "ws", tmp_path,
        compile_fn=lambda *_: (_ for _ in ()).throw(AssertionError("must stay idle")),
        load_slice_fn=lambda *_: {},
    )
    assert result["status"] == "NO_REQUESTS"


def test_reconcile_issue_frontier_persists_live_actionable_work(tmp_path):
    snapshot={"projects":[{"id":"rocksmith","repositories":[
        {"name":"jweter/rocksmith-cdlc-generator","open_issues_complete":True,
         "open_issues":[{"number":7,"title":"generation blocked","usability_impact":4}]}
    ]}]}
    result=reconcile_issue_frontier(snapshot,tmp_path)
    assert result["status"]=="ACTIONABLE_ISSUES"
    assert result["ranked_actionable"][0]["work_id"]=="issue:jweter/rocksmith-cdlc-generator#7"
    persisted=json.loads((tmp_path/"issue-work-frontier.json").read_text(encoding="utf-8"))
    assert persisted["issue_debt"]["rocksmith"]["open_total"]==1


def test_reconcile_issue_frontier_fails_closed_on_partial_inventory(tmp_path):
    snapshot={"projects":[{"id":"rocksmith","repositories":[
        {"name":"jweter/rocksmith-cdlc-generator","open_issues_complete":False,"open_issues":[]}
    ]}]}
    try:
        reconcile_issue_frontier(snapshot,tmp_path)
    except ValueError as exc:
        assert "incomplete" in str(exc)
    else:
        raise AssertionError("partial live issue evidence must not schedule work")


def test_reconcile_issue_cycle_rejects_stale_snapshot(tmp_path):
    snapshot={"collected_at_utc":"2020-01-01T00:00:00+00:00","projects":[]}
    (tmp_path/"github-portfolio-snapshot.json").write_text(json.dumps(snapshot),encoding="utf-8")
    result=reconcile_issue_cycle(tmp_path)
    assert result["status"]=="ISSUE_EVIDENCE_UNAVAILABLE"
    assert "stale" in result["reason"].lower()


def test_reconcile_issue_frontier_terminal_disposition_not_actionable(tmp_path):
    snapshot={"projects":[{"id":"rocksmith","repositories":[
        {"name":"jweter/rocksmith-cdlc-generator","open_issues_complete":True,
         "open_issues":[{"number":9,"title":"already handled"}]}
    ]}]}
    (tmp_path/"issue-dispositions.json").write_text(json.dumps({
        "issue:jweter/rocksmith-cdlc-generator#9":{"engineering_state":"CLOSED_VERIFIED"}
    }),encoding="utf-8")
    result=reconcile_issue_frontier(snapshot,tmp_path)
    assert result["ranked_actionable"]==[]


def test_reconcile_issue_cycle_recovers_stale_snapshot_with_live_refresh(tmp_path):
    stale={"collected_at_utc":"2020-01-01T00:00:00+00:00","projects":[]}
    (tmp_path/"github-portfolio-snapshot.json").write_text(json.dumps(stale),encoding="utf-8")
    fresh={"projects":[{"id":"rocksmith","repositories":[{"name":"jweter/rocksmith-cdlc-generator","open_issues_complete":True,"open_issues":[{"number":7,"title":"real work"}]}]}]}
    result=reconcile_issue_cycle(tmp_path, refresh_fn=lambda: fresh)
    assert result["status"]=="ACTIONABLE_ISSUES"
    assert result["evidence_recovery"]=="LIVE_REFRESH"
    assert result["ranked_actionable"][0]["work_id"]=="issue:jweter/rocksmith-cdlc-generator#7"
    assert json.loads((tmp_path/"github-portfolio-snapshot.json").read_text(encoding="utf-8")) == fresh


def test_reconcile_issue_cycle_reports_refresh_failure_without_hiding_staleness(tmp_path):
    stale={"collected_at_utc":"2020-01-01T00:00:00+00:00","projects":[]}
    (tmp_path/"github-portfolio-snapshot.json").write_text(json.dumps(stale),encoding="utf-8")
    def fail():
        raise RuntimeError("auth unavailable")
    result=reconcile_issue_cycle(tmp_path, refresh_fn=fail)
    assert result["status"]=="ISSUE_EVIDENCE_UNAVAILABLE"
    assert "stale" in result["reason"].lower()
    assert "auth unavailable" in result["reason"]
