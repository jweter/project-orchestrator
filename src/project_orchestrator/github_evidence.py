from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from .inventory import load_inventory


class GitHubError(RuntimeError):
    """Raised when live GitHub evidence cannot be collected safely."""


class GitHubNotFound(GitHubError):
    """Raised when an optional GitHub resource does not exist."""


class Transport(Protocol):
    def get_json(self, path: str) -> Any: ...


@dataclass
class UrllibGitHubTransport:
    token: str | None = None
    api_root: str = "https://api.github.com"

    @classmethod
    def from_environment(cls) -> "UrllibGitHubTransport":
        return cls(token=os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN"))

    def get_json(self, path: str) -> Any:
        url = path if path.startswith("https://") else f"{self.api_root}{path}"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "project-orchestrator",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise GitHubNotFound(path) from exc
            body = exc.read().decode("utf-8", errors="replace")[-1000:]
            raise GitHubError(f"GitHub request failed ({exc.code}) for {path}: {body}") from exc
        except urllib.error.URLError as exc:
            raise GitHubError(f"GitHub request failed for {path}: {exc.reason}") from exc


def _repo_path(repository: str) -> str:
    owner, name = repository.split("/", 1)
    return f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(name)}"


def _check_summary(check: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": check.get("id"),
        "name": check.get("name"),
        "status": check.get("status"),
        "conclusion": check.get("conclusion"),
        "started_at": check.get("started_at"),
        "completed_at": check.get("completed_at"),
        "details_url": check.get("details_url") or check.get("html_url"),
    }


def _normalized_review_gate(reviews: list[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Return review summaries and active CHANGES_REQUESTED count.

    COMMENTED reviews do not override a prior decisive review. DISMISSED clears
    the reviewer's active decisive state. APPROVED and CHANGES_REQUESTED replace
    the reviewer's previous decisive state.
    """
    summaries: list[dict[str, Any]] = []
    active: dict[str, str] = {}
    for review in sorted(
        reviews,
        key=lambda item: (
            str(item.get("submitted_at") or ""),
            int(item.get("id") or 0),
        ),
    ):
        user = review.get("user", {}).get("login")
        state = str(review.get("state") or "").upper()
        summaries.append(
            {
                "id": review.get("id"),
                "state": state or None,
                "user": user,
                "submitted_at": review.get("submitted_at"),
            }
        )
        if not user:
            continue
        if state in {"APPROVED", "CHANGES_REQUESTED"}:
            active[user] = state
        elif state == "DISMISSED":
            active.pop(user, None)

    blocking = sum(state == "CHANGES_REQUESTED" for state in active.values())
    return summaries, blocking


class GitHubEvidenceCollector:
    """Collect raw live evidence without deciding which checks are required."""

    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    def _paginate(self, path: str, *, per_page: int = 100, max_pages: int = 100) -> list[Any]:
        """Fetch an exhaustive list endpoint or fail closed.

        The GitHub REST API caps most list endpoints at 100 items per page.
        Explicit pagination prevents silently dropping a failing PR or active
        branch when a repository grows beyond one page.
        """
        items: list[Any] = []
        separator = "&" if "?" in path else "?"
        for page in range(1, max_pages + 1):
            payload = self.transport.get_json(
                f"{path}{separator}per_page={per_page}&page={page}"
            )
            if not isinstance(payload, list):
                raise GitHubError(f"expected list response while paginating {path}")
            items.extend(payload)
            if len(payload) < per_page:
                return items
        raise GitHubError(
            f"pagination exceeded {max_pages} pages for {path}; evidence is incomplete"
        )

    def _paginate_check_runs(
        self, path: str, *, per_page: int = 100, max_pages: int = 100
    ) -> list[Mapping[str, Any]]:
        checks: list[Mapping[str, Any]] = []
        separator = "&" if "?" in path else "?"
        for page in range(1, max_pages + 1):
            payload = self.transport.get_json(
                f"{path}{separator}per_page={per_page}&page={page}"
            )
            if not isinstance(payload, Mapping):
                raise GitHubError(f"expected object response while paginating {path}")
            page_checks = payload.get("check_runs")
            if not isinstance(page_checks, list):
                raise GitHubError(f"missing check_runs while paginating {path}")
            checks.extend(page_checks)
            if len(page_checks) < per_page:
                return checks
        raise GitHubError(
            f"check-run pagination exceeded {max_pages} pages for {path}; evidence is incomplete"
        )

    def collect_repository(self, repository: str) -> dict[str, Any]:
        root = _repo_path(repository)
        metadata = self.transport.get_json(root)
        if not isinstance(metadata, Mapping):
            raise GitHubError(f"repository metadata is not an object for {repository}")
        default_branch = metadata["default_branch"]
        default_commit = self.transport.get_json(
            f"{root}/commits/{urllib.parse.quote(str(default_branch), safe='')}"
        )
        if not isinstance(default_commit, Mapping):
            raise GitHubError(f"default commit metadata is not an object for {repository}")

        pulls = self._paginate(f"{root}/pulls?state=open")
        issues = self._paginate(
            f"{root}/issues?state=open&sort=updated&direction=desc"
        )
        branches = self._paginate(f"{root}/branches")
        commits = self._paginate(f"{root}/commits")

        open_prs = [self._collect_pr(root, pr["number"]) for pr in pulls]
        control_plane = self._collect_control_plane(root, str(default_branch))

        return {
            "name": repository,
            "default_branch": default_branch,
            "default_head_sha": default_commit.get("sha"),
            "archived": bool(metadata.get("archived")),
            "disabled": bool(metadata.get("disabled")),
            "control_plane": control_plane,
            "open_prs": open_prs,
            "recent_open_issues": [
                {
                    "number": issue.get("number"),
                    "title": issue.get("title"),
                    "updated_at": issue.get("updated_at"),
                    "labels": [label.get("name") for label in issue.get("labels", [])],
                }
                for issue in issues[:20]
                if "pull_request" not in issue
            ],
            "branches": [
                {
                    "name": item.get("name"),
                    "sha": item.get("commit", {}).get("sha"),
                    "protected": bool(item.get("protected")),
                }
                for item in branches
            ],
            "recent_commits": [
                {
                    "sha": item.get("sha"),
                    "message": item.get("commit", {}).get("message"),
                    "authored_at": item.get("commit", {}).get("author", {}).get("date"),
                }
                for item in commits[:20]
            ],
        }

    def _collect_pr(self, root: str, number: int) -> dict[str, Any]:
        detail = self.transport.get_json(f"{root}/pulls/{number}")
        if not isinstance(detail, Mapping):
            raise GitHubError(f"pull request {number} metadata is not an object")

        head_sha = detail.get("head", {}).get("sha")
        if not head_sha:
            raise GitHubError(f"pull request {number} has no head SHA")

        checks = self._paginate_check_runs(f"{root}/commits/{head_sha}/check-runs")
        reviews = self._paginate(f"{root}/pulls/{number}/reviews")
        review_comments = self._paginate(f"{root}/pulls/{number}/comments")
        conversation_comments = self._paginate(f"{root}/issues/{number}/comments")
        review_summaries, blocking_reviews = _normalized_review_gate(reviews)

        all_comments = [*review_comments, *conversation_comments]
        blocking_comments_state = "CLEAR" if not all_comments else "UNKNOWN"

        return {
            "number": number,
            "title": detail.get("title"),
            "draft": bool(detail.get("draft")),
            "head_ref": detail.get("head", {}).get("ref"),
            "head_sha": head_sha,
            "base_ref": detail.get("base", {}).get("ref"),
            "mergeable": detail.get("mergeable"),
            "mergeable_state": detail.get("mergeable_state"),
            "updated_at": detail.get("updated_at"),
            "check_runs": [_check_summary(check) for check in checks],
            "required_checks": [],
            "required_checks_state": "UNKNOWN",
            "reviews": review_summaries,
            "blocking_reviews": blocking_reviews,
            "review_gate_state": "BLOCKED" if blocking_reviews else "CLEAR",
            "review_comments": [
                {
                    "id": comment.get("id"),
                    "user": comment.get("user", {}).get("login"),
                    "created_at": comment.get("created_at"),
                    "updated_at": comment.get("updated_at"),
                }
                for comment in review_comments
            ],
            "conversation_comments": [
                {
                    "id": comment.get("id"),
                    "user": comment.get("user", {}).get("login"),
                    "created_at": comment.get("created_at"),
                    "updated_at": comment.get("updated_at"),
                }
                for comment in conversation_comments
            ],
            "blocking_comments": 0 if blocking_comments_state == "CLEAR" else None,
            "blocking_comments_state": blocking_comments_state,
        }

    def _collect_control_plane(self, root: str, default_branch: str) -> dict[str, Any]:
        path = "engineering/control-plane.json"
        encoded_path = urllib.parse.quote(path, safe="/")
        encoded_ref = urllib.parse.quote(default_branch, safe="")
        try:
            payload = self.transport.get_json(
                f"{root}/contents/{encoded_path}?ref={encoded_ref}"
            )
        except GitHubNotFound:
            return {"present": False}

        if not isinstance(payload, Mapping):
            return {"present": True, "parse_state": "UNAVAILABLE"}

        content = payload.get("content")
        if not isinstance(content, str):
            return {
                "present": True,
                "sha": payload.get("sha"),
                "parse_state": "UNAVAILABLE",
            }

        try:
            decoded = base64.b64decode(content).decode("utf-8")
            manifest = json.loads(decoded)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return {
                "present": True,
                "sha": payload.get("sha"),
                "parse_state": "MALFORMED",
            }

        if not isinstance(manifest, Mapping):
            return {
                "present": True,
                "sha": payload.get("sha"),
                "parse_state": "MALFORMED",
            }

        return {
            "present": True,
            "sha": payload.get("sha"),
            "parse_state": "PARSED",
            "schema_version": manifest.get("schema_version"),
            "preflight": manifest.get("preflight"),
            "promotion": manifest.get("promotion"),
            "verification": {
                "mandatory_check_names": (
                    manifest.get("verification", {}).get("mandatory_check_names")
                    if isinstance(manifest.get("verification"), Mapping)
                    else None
                )
            },
            "authoritative_documents": manifest.get("authoritative_documents"),
        }


def collect_portfolio(inventory_path: str, transport: Transport) -> dict[str, Any]:
    inventory = load_inventory(inventory_path)
    collector = GitHubEvidenceCollector(transport)
    projects: list[dict[str, Any]] = []
    for project in inventory["projects"]:
        repositories = [
            collector.collect_repository(entry["name"])
            for entry in project["repositories"]
        ]
        projects.append(
            {
                "id": project["id"],
                "name": project["name"],
                "coordination": project["coordination"],
                "repositories": repositories,
            }
        )
    return {
        "schema_version": 1,
        "evidence_kind": "raw-live-github",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "required_check_interpretation": "UNKNOWN",
        "projects": projects,
    }
