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


def repo_responses(repo: str, *, control_plane: bool = True) -> dict[str, object]:
    root = f"/repos/{repo}"
    manifest = base64.b64encode(json.dumps({
        "schema_version": 4,
        "preflight": {"required_before_pr": True},
        "promotion": {"states": ["IMPLEMENTING", "GREEN"]},
        "authoritative_documents": ["docs/roadmap.md"],
    }).encode()).decode()
    responses: dict[str, object] = {
        root: {"default_branch": "main", "archived": False, "disabled": False},
        f"{root}/commits/main": {"sha": "main-sha"},
        f"{root}/pulls?state=open&per_page=100": [{
            "number": 7,
        }],
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
        f"{root}/commits/head-sha/check-runs?per_page=100": {
            "check_runs": [{
                "id": 1,
                "name": "CI",
                "status": "completed",
                "conclusion": "success",
            }]
        },
        f"{root}/pulls/7/reviews?per_page=100": [],
        f"{root}/pulls/7/comments?per_page=100": [],
        f"{root}/issues?state=open&sort=updated&direction=desc&per_page=20": [
            {"number": 8, "title": "real issue", "updated_at": "now", "labels": [{"name": "bug"}]},
            {"number": 7, "title": "PR shadow", "pull_request": {}, "updated_at": "now", "labels": []},
        ],
        f"{root}/branches?per_page=100": [
            {"name": "main", "commit": {"sha": "main-sha"}, "protected": True},
            {"name": "fix/pr", "commit": {"sha": "head-sha"}, "protected": False},
        ],
        f"{root}/commits?per_page=20": [{
            "sha": "main-sha",
            "commit": {"message": "latest", "author": {"date": "now"}},
        }],
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
    evidence = GitHubEvidenceCollector(transport).collect_repository("jweter/knowledge-engine-core")
    pr = evidence["open_prs"][0]
    assert pr["check_runs"][0]["name"] == "CI"
    assert pr["required_checks"] == []
    assert pr["required_checks_state"] == "UNKNOWN"
    assert evidence["control_plane"]["parse_state"] == "PARSED"
    assert evidence["recent_open_issues"] == [{
        "number": 8,
        "title": "real issue",
        "updated_at": "now",
        "labels": ["bug"],
    }]


def test_missing_control_plane_is_explicit_not_guessed() -> None:
    transport = FakeTransport(repo_responses("jweter/Project-Everward", control_plane=False))
    evidence = GitHubEvidenceCollector(transport).collect_repository("jweter/Project-Everward")
    assert evidence["control_plane"] == {"present": False}


def test_portfolio_collection_uses_inventory_and_keeps_three_project_boundary(tmp_path) -> None:
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
                "repositories": [{"name": "jweter/rocksmith-cdlc-generator", "role": "standalone"}],
            },
            {
                "id": "everward",
                "name": "Everward",
                "coordination": "standalone",
                "repositories": [{"name": "jweter/Project-Everward", "role": "standalone"}],
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
