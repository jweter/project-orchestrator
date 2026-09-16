import pytest

from project_orchestrator.candidate_sources import CandidateSourceError, adapt_snapshot, issue_candidate, pull_request_candidate


def test_issue_without_decomposition_never_invents_dispatch_scope():
    item = issue_candidate({"repository": "jweter/knowledge-engine-ai", "number": 129, "state": "open", "updated_at": "rev"})
    assert item["project"] == "knowledge-engine"
    assert item["paths"] == []
    assert item["task"] is None
    assert item["conflict_free"] is False


def test_explicit_decomposition_supplies_bounded_issue_scope():
    item = issue_candidate(
        {"repository": "jweter/rocksmith-cdlc-generator", "number": 612, "state": "open", "updated_at": "rev"},
        decomposition={"task": "wire sanitized PSARC evidence", "paths": ["src/runner.py"], "conflict_free": True, "priority": "P5", "base_sha": "abc"},
    )
    assert item["project"] == "rocksmith"
    assert item["task"] == "wire sanitized PSARC evidence"
    assert item["paths"] == ["src/runner.py"]
    assert item["conflict_free"] is True


def test_pr_uses_exact_head_as_revision_and_existing_work_priority():
    item = pull_request_candidate({"repository": "jweter/Project-Everward", "number": 300, "state": "open", "head_sha": "deadbeef", "head_branch": "agent/x", "paths": ["Source/X.cpp"], "conflict_free": True})
    assert item["source_revision"] == "deadbeef"
    assert item["priority"] == "P2"
    assert item["branch"] == "agent/x"


def test_merged_pr_becomes_terminal_source():
    item = pull_request_candidate({"repository": "jweter/knowledge-engine-core", "number": 503, "state": "closed", "merged": True, "head_sha": "abc"})
    assert item["source_state"] == "MERGED"


def test_product_reality_is_always_blocking_authority():
    items = adapt_snapshot({"product_reality_debt": [{"repository": "jweter/Project-Everward", "id": "243", "state": "open", "revision": "1", "task": "real Windows Unreal verification"}]})
    assert items[0]["product_reality_required"] is True


def test_snapshot_order_is_deterministic_and_ke_is_one_family():
    snapshot = {
        "issues": [
            {"repository": "jweter/knowledge-engine-web", "number": 107, "state": "open", "updated_at": "1"},
            {"repository": "jweter/knowledge-engine-core", "number": 449, "state": "open", "updated_at": "1"},
        ]
    }
    items = adapt_snapshot(snapshot)
    assert [item["project"] for item in items] == ["knowledge-engine", "knowledge-engine"]
    assert [item["repository"] for item in items] == sorted(item["repository"] for item in items)


def test_non_product_repository_fails_closed():
    with pytest.raises(CandidateSourceError):
        issue_candidate({"repository": "jweter/project-orchestrator", "number": 98, "state": "open", "updated_at": "1"})
