"""IRON CONTINUUM: independent current-head evidence is required before merging."""

from __future__ import annotations

import json

from project_orchestrator import iron_god_portfolio_merger as merger

REPO = "jweter/knowledge-engine-core"
HEAD = "a" * 40
STALE = "b" * 40


def _port(monkeypatch, *, comments=None, approvals=None, resolved=HEAD):
    port = merger.GhPort()
    rows = comments if comments is not None else []
    reviews = approvals if approvals is not None else []

    def fake_gh(args, *, allow_missing=False):
        path = args[-1]
        if path == f"repos/{REPO}/pulls/7":
            return json.dumps({"user": {"login": "jweter"}})
        if path.startswith(f"repos/{REPO}/commits/"):
            return json.dumps({
                "sha": resolved,
                "commit": {"committer": {"date": "2020-01-01T00:00:00Z"}},
            })
        raise AssertionError(args)

    def fake_paged(endpoint):
        if "/issues/7/comments" in endpoint:
            return rows
        if "/pulls/7/reviews" in endpoint:
            return reviews
        if "/pulls/7/comments" in endpoint or "/issues/7/events" in endpoint:
            return []
        raise AssertionError(endpoint)

    monkeypatch.setattr(port, "_gh", fake_gh)
    monkeypatch.setattr(port, "_paged", fake_paged)
    return port


def test_bot_clean_exact_head_rejected_when_hash_resolves_to_different_commit(monkeypatch):
    note = {
        "user": {"login": "chatgpt-codex-connector[bot]"},
        "body": "Codex Review: Didn't find any major issues.\n"
        "**Reviewed commit:** `aaaaaaaaaa`",
    }
    port = _port(monkeypatch, comments=[note], resolved=HEAD)
    assert port.independent_review(REPO, 7, HEAD, [])
    port = _port(monkeypatch, comments=[note], resolved=STALE)
    assert not port.independent_review(REPO, 7, HEAD, [])


def test_old_head_spoofed_and_after_merge_note_not_a_premerge_receipt(monkeypatch):
    wrong_head = {
        "user": {"login": "chatgpt-codex-connector[bot]"},
        "body": "Codex Review: Didn't find any major issues.\n"
        "**Reviewed commit:** `bbbbbbbbbb`",
    }
    port = _port(monkeypatch, comments=[wrong_head])
    assert not port.independent_review(REPO, 7, HEAD, [])
    forged = dict(wrong_head)
    forged["user"] = {"login": "jweter"}
    forged["body"] = forged["body"].replace("bbbbbbbbbb", "aaaaaaaaaa")
    port = _port(monkeypatch, comments=[forged])
    assert not port.independent_review(REPO, 7, HEAD, [])


def test_human_approval_must_be_independent_and_bound_to_exact_head(monkeypatch):
    port = _port(monkeypatch)
    approved = [{"user": {"login": "reviewer"}, "state": "APPROVED",
                 "author_association": "COLLABORATOR", "commit_id": HEAD}]
    assert port.independent_review(REPO, 7, HEAD, approved)
    approved[0]["author_association"] = "CONTRIBUTOR"
    assert not port.independent_review(REPO, 7, HEAD, approved)
    approved[0]["author_association"] = "COLLABORATOR"
    approved[0]["commit_id"] = STALE
    assert not port.independent_review(REPO, 7, HEAD, approved)
    approved[0]["commit_id"] = HEAD
    approved[0]["state"] = "COMMENTED"
    assert not port.independent_review(REPO, 7, HEAD, approved)


def test_portfolio_quiet_window_alone_does_not_grant_merge(monkeypatch):
    port = _port(monkeypatch)
    assert port.quiet_since(REPO, 7, HEAD, "2020-01-01T00:00:00Z") is None
    assert merger.evaluate(
        {
            "repository": REPO,
            "title": "ordinary PR",
            "state": "OPEN",
            "quiet_since": None,
        },
        now=merger.datetime.now(merger.UTC),
    ).action != merger.MERGE
