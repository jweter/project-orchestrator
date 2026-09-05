from __future__ import annotations

import base64
import json

from project_orchestrator.github_evidence import (
    GitHubEvidenceCollector,
    GitHubNotFound,
    collect_portfolio,
)


class FakeTransport:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get_json(self, path: str) -> object:
        self.calls.append(path)
        value = self.responses.get(path)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise AssertionError(f"unexpected GitHub request: {path}")
        return value


def repo_responses(
    repo: str,
    *,
    control_plane: bool = True,
    reviews: list[dict] | None = None,
    review_comments: list[dict] | None = None,
    conversation_comments: list[dict] | None = None,
) -> dict[str, object]:
    root = f"/repos/{repo}"
    manifest = base64.b64encode(
        json.dumps(
            {
                "schema_version": 4,
                "preflight": {"required_before_pr": True},
                "promotion": {"states": ["IMPLEMENTING", "GREEN"]},
                "authoritative_documents": ["docs/roadmap.md"],
            }
        ).encode()
    ).decode()
    responses: dict[str, object] = {
        root: {"default_branch": "main", "archived": False, "disabled": False},
        f"{root}/commits/main": {"sha": "main-sha"},
        f"{root}/pulls?state=open&per_page=100&page=1": [{"number": 7}],
        f"{root}/pulls/7": {
            "number": 7,
            "title": "fix existing work",
            "draft": False,
            "head": {"sha": "head-sha", "ref": "fix/pr"},
            "base": {"ref": "main"},
            "mergeable": True,
            "mergeable_state": "clean",
            "updated_at": "2026-09-05T00:00:00Z",
        },
        f"{root}/commits/head-sha/check-runs?per_page=100&page=1": {
            "check_runs": [
                {
                    "id": 1,
                    "name": "CI",
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        },
        f"{root}/pulls/7/reviews?per_page=100&page=1": reviews or [],
        f"{root}/pulls/7/comments?per_page=100&page=1": review_comments or [],
        f"{root}/issues/7/comments?per_page=100&page=1": conversation_comments or [],
        f"{root}/issues?state=open&sort=updated&direction=desc&per_page=100&page=1": [
            {
                "number": 8,
                "title": "real issue",
                "updated_at": "now",
                "labels": [{"name": "bug"}],
            },
            {
                "number": 7,
                "title": "PR shadow",
                "pull_request": {},
                "updated_at": "now",
                "labels": [],
            },
        ],
        f"{root}/branches?per_page=100&page=1": [
            {"name": "main", "commit": {"sha": "main-sha"}, "protected": True},
            {"name": "fix/pr", "commit": {"sha": "head-sha"}, "protected": False},
        ],
        f"{root}/commits?per_page=100&page=1": [
            {
                "sha": "main-sha",
                "commit": {"message": "latest", "author": {"date": "now"}},
            }
        ],
    }
    cp_path = f"{root}/contents/engineering/control-plane.json?ref=main"
    responses[cp_path] = (
        {"sha": "cp-sha", "content": manifest}
        if control_plane
        else GitHubNotFound(cp_path)
    )
    return responses


def test_repository_collection_preserves_raw_checks_without_declaring_required() -> None:
    transport = FakeTransport(repo_responses("jweter/knowledge-engine-core"))
    evidence = GitHubEvidenceCollector(transport).collect_repository(
        "jweter/knowledge-engine-core"
    )
    pr = evidence["open_prs"][0]
    assert pr["check_runs"][0]["name"] == "CI"
    assert pr["required_checks"] == []
    assert pr["required_checks_state"] == "UNKNOWN"
    assert pr["blocking_reviews"] == 0
    assert pr["blocking_comments_state"] == "CLEAR"
    assert evidence["control_plane"]["parse_state"] == "PARSED"
    assert evidence["recent_open_issues"] == [
        {
            "number": 8,
            "title": "real issue",
            "updated_at": "now",
            "labels": ["bug"],
        }
    ]


def test_changes_requested_review_is_normalized_as_blocking() -> None:
    reviews = [
        {
            "id": 1,
            "state": "CHANGES_REQUESTED",
            "user": {"login": "reviewer"},
            "submitted_at": "2026-09-05T00:00:00Z",
        },
        {
            "id": 2,
            "state": "COMMENTED",
            "user": {"login": "reviewer"},
            "submitted_at": "2026-09-05T00:01:00Z",
        },
    ]
    evidence = GitHubEvidenceCollector(
        FakeTransport(repo_responses("jweter/knowledge-engine-core", reviews=reviews))
    ).collect_repository("jweter/knowledge-engine-core")
    assert evidence["open_prs"][0]["blocking_reviews"] == 1
    assert evidence["open_prs"][0]["review_gate_state"] == "BLOCKED"


def test_newer_approval_clears_prior_changes_requested() -> None:
    reviews = [
        {
            "id": 1,
            "state": "CHANGES_REQUESTED",
            "user": {"login": "reviewer"},
            "submitted_at": "2026-09-05T00:00:00Z",
        },
        {
            "id": 2,
            "state": "APPROVED",
            "user": {"login": "reviewer"},
            "submitted_at": "2026-09-05T00:02:00Z",
        },
    ]
    evidence = GitHubEvidenceCollector(
        FakeTransport(repo_responses("jweter/knowledge-engine-core", reviews=reviews))
    ).collect_repository("jweter/knowledge-engine-core")
    assert evidence["open_prs"][0]["blocking_reviews"] == 0


def test_conversation_comments_are_collected_and_fail_closed() -> None:
    comments = [
        {
            "id": 99,
            "user": {"login": "maintainer"},
            "created_at": "now",
            "updated_at": "now",
        }
    ]
    evidence = GitHubEvidenceCollector(
        FakeTransport(
            repo_responses(
                "jweter/knowledge-engine-core",
                conversation_comments=comments,
            )
        )
    ).collect_repository("jweter/knowledge-engine-core")
    pr = evidence["open_prs"][0]
    assert pr["conversation_comments"][0]["id"] == 99
    assert pr["blocking_comments"] is None
    assert pr["blocking_comments_state"] == "UNKNOWN"


def test_pagination_reads_more_than_one_page() -> None:
    path = "/repos/jweter/example/branches"
    transport = FakeTransport(
        {
            f"{path}?per_page=100&page=1": [{"name": f"b-{i}"} for i in range(100)],
            f"{path}?per_page=100&page=2": [{"name": "last"}],
        }
    )
    items = GitHubEvidenceCollector(transport)._paginate(path)
    assert len(items) == 101
    assert items[-1]["name"] == "last"


def test_missing_control_plane_is_explicit_not_guessed() -> None:
    transport = FakeTransport(
        repo_responses("jweter/Project-Everward", control_plane=False)
    )
    evidence = GitHubEvidenceCollector(transport).collect_repository(
        "jweter/Project-Everward"
    )
    assert evidence["control_plane"] == {"present": False}


def test_non_object_control_plane_is_malformed() -> None:
    repo = "jweter/knowledge-engine-core"
    responses = repo_responses(repo)
    root = f"/repos/{repo}"
    responses[f"{root}/contents/engineering/control-plane.json?ref=main"] = {
        "sha": "cp-sha",
        "content": base64.b64encode(b"[]").decode(),
    }
    evidence = GitHubEvidenceCollector(FakeTransport(responses)).collect_repository(repo)
    assert evidence["control_plane"]["parse_state"] == "MALFORMED"


def test_portfolio_collection_uses_inventory_and_keeps_three_project_boundary(
    tmp_path,
) -> None:
    inventory = {
        "schema_version": 1,
        "projects": [
            {
                "id": "knowledge-engine",
                "name": "Knowledge Engine",
                "coordination": "cross-repo-family",
                "repositories": [
                    {"name": "jweter/knowledge-engine-core", "role": "core"},
                    {"name": "jweter/knowledge-engine-web", "role": "web"},
                    {"name": "jweter/knowledge-engine-ai", "role": "ai"},
                ],
            },
            {
                "id": "rocksmith",
                "name": "Rocksmith",
                "coordination": "standalone",
                "repositories": [
                    {"name": "jweter/rocksmith-cdlc-generator", "role": "standalone"}
                ],
            },
            {
                "id": "everward",
                "name": "Everward",
                "coordination": "standalone",
                "repositories": [
                    {"name": "jweter/Project-Everward", "role": "standalone"}
                ],
            },
        ],
    }
    path = tmp_path / "portfolio.json"
    path.write_text(json.dumps(inventory), encoding="utf-8")

    responses: dict[str, object] = {}
    for repository in [
        "jweter/knowledge-engine-core",
        "jweter/knowledge-engine-web",
        "jweter/knowledge-engine-ai",
        "jweter/rocksmith-cdlc-generator",
        "jweter/Project-Everward",
    ]:
        responses.update(repo_responses(repository))

    snapshot = collect_portfolio(str(path), FakeTransport(responses))
    assert [project["id"] for project in snapshot["projects"]] == [
        "knowledge-engine",
        "rocksmith",
        "everward",
    ]
    assert snapshot["required_check_interpretation"] == "UNKNOWN"
    assert sum(len(project["repositories"]) for project in snapshot["projects"]) == 5
