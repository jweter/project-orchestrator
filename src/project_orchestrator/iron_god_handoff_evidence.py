"""Opt-in, read-only GitHub evidence for a scoped advisory handoff.

Never invoked by the resident pass. A successful check is not an execution grant.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import iron_god_structured_handoff as structured
from . import mcp_coordination_accountability as accountability
from .github_evidence import GitHubError, Transport, _repo_path


def verify_handoff(
    path: Path, *, participant: str, request_id: str, transport: Transport,
    now: datetime | None = None,
) -> dict[str, Any]:
    envelope = structured.read_engineering_handoff(
        path, participant=participant, request_id=request_id,
    )
    payload = envelope["payload"]
    repository, sha, pr = payload["repository"], payload["commit_sha"], payload["pull_request"]
    moment = now if now is not None else datetime.now(timezone.utc)
    if moment.utcoffset() is None:
        raise ValueError("INVALID_ASSESSMENT_TIME")
    result: dict[str, Any] = {
        "request_id": request_id, "repository": repository, "commit_sha": sha,
        "pull_request": pr, "observed_at_utc": moment.isoformat(),
        "status": "UNVERIFIED", "reason": "REMOTE_EVIDENCE_UNAVAILABLE",
        "commit_verified": False, "remote_head_verified": False,
        "verified_tests": [], "test_evidence_verified": False,
        "dependencies_verified": False, "can_execute": False,
    }
    deadline = envelope["deadline_at_utc"]
    if deadline is not None and accountability._time(deadline) <= moment:
        return {**result, "status": "EXPIRED", "reason": "HANDOFF_DEADLINE_ELAPSED"}
    root = _repo_path(repository)
    try:
        commit = transport.get_json(f"{root}/commits/{sha}")
        if not isinstance(commit, dict) or commit.get("sha") != sha:
            return {**result, "reason": "COMMIT_MISMATCH"}
        result["commit_verified"] = True
        if pr is None:
            return {**result, "reason": "NO_PULL_REQUEST_HEAD"}
        detail = transport.get_json(f"{root}/pulls/{pr}")
        if not _matches_pr(detail, repository, sha, pr):
            return {**result, "reason": "PULL_REQUEST_HEAD_MISMATCH"}
        # Only explicit GitHub Actions run IDs are independently resolvable.
        # Legacy/local test labels stay unverified, even if other runs pass.
        verified = []
        for claim in payload["tests"]:
            prefix, _, number = claim.partition(":")
            if prefix != "actions" or not number.isascii() or not number.isdecimal() or len(number) > 19:
                continue
            run_id = int(number)
            if not 0 < run_id <= 2**63 - 1 or str(run_id) != number:
                continue
            run = transport.get_json(f"{root}/actions/runs/{run_id}")
            if _matches_run(run, repository, sha, run_id):
                verified.append(claim)
        # Re-read the head after collecting checks to detect intervening changes.
        if not _matches_pr(transport.get_json(f"{root}/pulls/{pr}"), repository, sha, pr):
            return {**result, "reason": "PULL_REQUEST_HEAD_CHANGED"}
        result["remote_head_verified"] = True
        result["verified_tests"] = verified
        result["test_evidence_verified"] = bool(payload["tests"]) and verified == payload["tests"]
        result["status"] = "REMOTE_HEAD_OBSERVED"
        result["reason"] = "DEPENDENCIES_AND_AUTHORITY_NOT_VERIFIED"
        return result
    except (GitHubError, OSError, ValueError):
        return result


def _matches_pr(detail: Any, repository: str, sha: str, number: int) -> bool:
    if not isinstance(detail, dict) or type(detail.get("number")) is not int:
        return False
    head, base = detail.get("head"), detail.get("base")
    return (
        detail["number"] == number and detail.get("state") == "open"
        and isinstance(head, dict) and head.get("sha") == sha
        and _repo_matches(head.get("repo"), repository)
        and isinstance(base, dict) and _repo_matches(base.get("repo"), repository)
    )


def _repo_matches(value: Any, repository: str) -> bool:
    return isinstance(value, dict) and value.get("full_name") == repository


def _matches_run(run: Any, repository: str, sha: str, run_id: int) -> bool:
    return (
        isinstance(run, dict) and type(run.get("id")) is int and run["id"] == run_id
        and run.get("head_sha") == sha and run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and _repo_matches(run.get("repository"), repository)
        and _repo_matches(run.get("head_repository"), repository)
    )
