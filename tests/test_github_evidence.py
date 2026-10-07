from __future__ import annotations

import base64
import json

from project_orchestrator.github_evidence import GitHubEvidenceCollector, GitHubNotFound


class FakeTransport:
    def __init__(self, responses):
        self.responses = responses

    def get_json(self, path):
        value = self.responses.get(path)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise AssertionError(f"unexpected GitHub request: {path}")
        return value


def repo_responses(repo: str):
    root = f"/repos/{repo}"
    manifest = base64.b64encode(json.dumps({
        "schema_version": 4,
        "preflight": {"required_before_pr": True},
        "promotion": {"states": ["IMPLEMENTING", "GREEN"]},
        "verification": {"mandatory_check_names": ["CI"]},
        "authoritative_documents": ["docs/roadmap.md"],
    }).encode()).decode()
    return {
        root: {"default_branch": "main", "archived": False, "disabled": False},
        f"{root}/commits/main": {"sha": "main-sha"},
        f"{root}/pulls?state=open&per_page=100&page=1": [{"number": 7}],
        f"{root}/pulls/7": {
            "number": 7, "title": "fix existing work", "draft": False,
            "head": {"sha": "head-sha", "ref": "fix/pr"}, "base": {"ref": "main"},
            "mergeable": True, "mergeable_state": "clean", "updated_at": "now",
        },
        f"{root}/commits/head-sha/check-runs?per_page=100&page=1": {"check_runs": [{
            "id": 1, "name": "CI", "status": "completed", "conclusion": "success",
            "app": {"id": 15368}, "completed_at": "2026-09-05T00:00:01Z",
        }]},
        f"{root}/commits/head-sha/status": {"statuses": [{
            "id": 2, "context": "legacy-ci", "state": "success",
            "created_at": "2026-09-05T00:00:00Z", "updated_at": "2026-09-05T00:00:02Z",
            "target_url": "https://example.invalid",
        }]},
        f"{root}/pulls/7/reviews?per_page=100&page=1": [],
        f"{root}/pulls/7/comments?per_page=100&page=1": [],
        f"{root}/issues/7/comments?per_page=100&page=1": [],
        f"{root}/issues?state=open&sort=updated&direction=desc&per_page=100&page=1": [],
        f"{root}/branches?per_page=100&page=1": [],
        f"{root}/commits?per_page=100&page=1": [],
        f"{root}/contents/engineering/control-plane.json?ref=main": {"sha": "cp-sha", "content": manifest},
    }


def test_repository_collection_preserves_app_and_commit_status_evidence() -> None:
    evidence = GitHubEvidenceCollector(FakeTransport(repo_responses("jweter/example"))).collect_repository("jweter/example")
    pr = evidence["open_prs"][0]
    assert pr["check_runs"][0]["app_id"] == 15368
    assert pr["check_runs"][0]["evidence_kind"] == "check_run"
    assert pr["status_contexts"][0]["name"] == "legacy-ci"
    assert pr["status_contexts"][0]["conclusion"] == "success"
    assert pr["status_contexts"][0]["evidence_kind"] == "commit_status"
    assert evidence["control_plane"]["verification"]["mandatory_check_names"] == ["CI"]


def test_missing_control_plane_is_explicit_not_guessed() -> None:
    responses = repo_responses("jweter/example")
    path = "/repos/jweter/example/contents/engineering/control-plane.json?ref=main"
    responses[path] = GitHubNotFound(path)
    evidence = GitHubEvidenceCollector(FakeTransport(responses)).collect_repository("jweter/example")
    assert evidence["control_plane"] == {"present": False}


def test_transport_uses_gh_cli_token_when_environment_missing(monkeypatch):
    from project_orchestrator.github_evidence import UrllibGitHubTransport
    import subprocess
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="cli-token\n", stderr=""))
    assert UrllibGitHubTransport.from_environment().token == "cli-token"


def test_transport_fails_closed_without_authenticated_route(monkeypatch):
    from project_orchestrator.github_evidence import GitHubError, UrllibGitHubTransport
    import subprocess
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    def fail(*a, **k):
        raise FileNotFoundError("gh unavailable")
    monkeypatch.setattr(subprocess, "run", fail)
    try:
        UrllibGitHubTransport.from_environment()
    except GitHubError as exc:
        assert "authenticated GitHub transport unavailable" in str(exc)
    else:
        raise AssertionError("anonymous GitHub fallback must never be used")
