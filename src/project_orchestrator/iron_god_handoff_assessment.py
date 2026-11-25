"""One bounded advisory assessment; no execution, review or acceptance authority."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import iron_god_handoff_dependencies as dependencies
from . import iron_god_handoff_evidence as evidence
from . import iron_god_required_coverage as coverage
from . import iron_god_structured_handoff as structured
from . import mcp_coordination_accountability as accountability
from .github_evidence import GitHubError, Transport, UrllibGitHubTransport, _repo_path

MAX_REMOTE_REQUESTS = 32


class BoundedTransport:
    def __init__(self, transport: Transport):
        self.transport = transport
        self.calls = 0

    def get_json(self, path: str) -> Any:
        if self.calls >= MAX_REMOTE_REQUESTS:
            raise GitHubError("ASSESSMENT_REQUEST_BUDGET_EXHAUSTED")
        self.calls += 1
        return self.transport.get_json(path)


def assess_handoff(
    database: Path, *, participant: str, request_id: str, release_repo: Path,
    state_root: Path, include_remote: bool = False, transport: Transport | None = None,
) -> dict[str, Any]:
    envelope = structured.read_engineering_handoff(
        database, participant=participant, request_id=request_id)
    payload = envelope["payload"]
    start = datetime.now(timezone.utc)
    result: dict[str, Any] = {
        "request_id": request_id, "repository": payload["repository"],
        "slice_id": payload["slice_id"], "milestone": payload["milestone"],
        "commit_sha": payload["commit_sha"], "pull_request": payload["pull_request"],
        "status": "INCOMPLETE", "observed_at_utc": start.isoformat(),
        "remote": {"status": "NOT_REQUESTED"}, "coverage": {"status": "NOT_REQUESTED"},
        "dependencies": {"status": "NOT_OBSERVED"}, "outstanding": [],
        "authority": "NOT_ASSESSED_BY_ADVISORY_TOOL", "can_execute": False,
        "product_acceptance": "UNVERIFIED", "review": "UNVERIFIED",
        "dependencies_verified": False,
    }
    deadline = envelope["deadline_at_utc"]

    def expired():
        return deadline is not None and accountability._time(deadline) <= datetime.now(timezone.utc)

    if expired():
        return {**result, "status": "EXPIRED", "outstanding": ["HANDOFF_EXPIRED"]}
    result["dependencies"] = dependencies.reconcile_dependencies(
        database, participant=participant, request_id=request_id,
        release_repo=release_repo, state_root=state_root)
    if include_remote and payload["pull_request"] is not None and not expired():
        try:
            bounded = BoundedTransport(transport if transport is not None else
                                       UrllibGitHubTransport.from_environment())
            result["remote"] = evidence.verify_handoff(
                database, participant=participant, request_id=request_id, transport=bounded)
            if result["remote"].get("remote_head_verified") is True and not expired():
                result["coverage"] = coverage.observe_required_coverage(
                    payload["repository"], payload["commit_sha"], payload["pull_request"], bounded)
                # Check the shared PR identity again after both independent readers.
                detail = bounded.get_json(
                    f'{_repo_path(payload["repository"])}/pulls/{payload["pull_request"]}')
                if not evidence._matches_pr(detail, payload["repository"],
                                            payload["commit_sha"], payload["pull_request"]):
                    raise GitHubError("ASSESSMENT_HEAD_CHANGED")
        except (GitHubError, OSError, ValueError, TypeError):
            result["remote"] = {"status": "UNVERIFIED", "reason": "REMOTE_ASSESSMENT_INCOMPLETE"}
            result["coverage"] = {"status": "UNVERIFIED"}
    if expired():
        return {**result, "status": "EXPIRED", "remote": {"status": "EXPIRED"},
                "coverage": {"status": "EXPIRED"}, "dependencies": {"status": "EXPIRED"},
                "outstanding": ["HANDOFF_EXPIRED"]}
    for section, field, reason in (
        ("remote", "remote_head_verified", "REMOTE_HEAD"),
        ("remote", "test_evidence_verified", "CLAIMED_TESTS"),
        ("coverage", "required_coverage_verified", "REQUIRED_CI"),
        ("dependencies", "canonical_dependencies_satisfied", "LOCAL_DEPENDENCIES"),
    ):
        if result[section].get(field) is not True:
            result["outstanding"].append(reason)
    # These independent gates are intentionally outside the assessment.
    result["outstanding"].extend(["INDEPENDENT_DEPENDENCY_ACCEPTANCE", "REVIEW",
                                  "PRODUCT_ACCEPTANCE", "OWNER_AUTHORITY"])
    return result
