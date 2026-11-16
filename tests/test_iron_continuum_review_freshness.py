"""IRON CONTINUUM: independent current-head evidence is required before merging."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

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
        "created_at": "2026-11-16T14:00:00Z",
        "updated_at": "2026-11-16T14:00:00Z",
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
                 "author_association": "COLLABORATOR", "commit_id": HEAD,
                 "submitted_at": "2026-11-16T13:00:00Z"}]
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


def test_edited_codex_receipt_is_untrusted_and_review_rejection_supersedes_approval(monkeypatch):
    clean = {
        "user": {"login": "chatgpt-codex-connector[bot]"},
        "body": "Codex Review: Didn't find any major issues.\n"
        "**Reviewed commit:** `aaaaaaaaaa`",
        "created_at": "2026-11-16T14:00:00Z",
        "updated_at": "2026-11-16T14:00:10Z",
    }
    port = _port(monkeypatch, comments=[clean])
    assert not port.independent_review(REPO, 7, HEAD, [])
    earlier = {"user": {"login": "reviewer"}, "commit_id": HEAD,
               "author_association": "COLLABORATOR", "state": "APPROVED",
               "submitted_at": "2026-11-16T14:00:00Z"}
    later = {**earlier, "state": "CHANGES_REQUESTED", "submitted_at": "2026-11-16T14:01:00Z"}
    assert not port.independent_review(REPO, 7, HEAD, [later, earlier])


def test_valid_recent_receipt_must_still_finish_five_minute_settlement(monkeypatch):
    recent = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    clean = {
        "user": {"login": "chatgpt-codex-connector[bot]"},
        "body": "Codex Review: Didn't find any major issues.\n"
        "**Reviewed commit:** `aaaaaaaaaa`",
        "created_at": recent,
        "updated_at": recent,
    }
    port = _port(monkeypatch, comments=[clean])
    assert port.independent_review(REPO, 7, HEAD, [])
    stamp = port.quiet_since(REPO, 7, HEAD, "2020-01-01T00:00:00Z")
    assert stamp is not None
    assert datetime.fromisoformat(stamp) >= datetime.now(UTC) - timedelta(seconds=30)


def test_portfolio_comment_only_review_cannot_withdraw_changes_request(monkeypatch):
    port = _port(monkeypatch)
    request = {
        "user": {"login": "reviewer1"}, "commit_id": HEAD,
        "author_association": "COLLABORATOR", "state": "CHANGES_REQUESTED",
        "submitted_at": "2026-11-16T13:00:00Z",
    }
    commented = {**request, "state": "COMMENTED",
                 "submitted_at": "2026-11-16T14:00:00Z"}
    other_approval = {
        "user": {"login": "reviewer2"}, "commit_id": HEAD,
        "author_association": "MEMBER", "state": "APPROVED",
        "submitted_at": "2026-11-16T14:10:00Z",
    }
    assert not port.independent_review(
        REPO, 7, HEAD, [request, commented, other_approval]
    )
    assert not port.independent_review(
        REPO, 7, HEAD, [other_approval, commented, request]
    )
    approved_later = {**request, "state": "APPROVED",
                      "submitted_at": "2026-11-16T14:20:00Z"}
    assert port.independent_review(REPO, 7, HEAD, [request, commented, approved_later])
    assert port.independent_review(REPO, 7, HEAD, [approved_later, commented, request])


def test_portfolio_comment_only_review_preserves_prior_approval(monkeypatch):
    port = _port(monkeypatch)
    approved = {
        "user": {"login": "reviewer"}, "commit_id": HEAD,
        "author_association": "COLLABORATOR", "state": "APPROVED",
        "submitted_at": "2026-11-16T13:00:00Z",
    }
    commented = {**approved, "state": "COMMENTED",
                 "submitted_at": "2026-11-16T14:00:00Z"}
    assert port.independent_review(REPO, 7, HEAD, [approved, commented])
    dismissed = {**approved, "state": "DISMISSED",
                 "submitted_at": "2026-11-16T14:20:00Z"}
    assert not port.independent_review(REPO, 7, HEAD, [approved, commented, dismissed])
