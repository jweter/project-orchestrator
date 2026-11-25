"""Bounded read-only required-check evidence, not an acceptance or execution gate."""
from __future__ import annotations

from typing import Any

from .github_evidence import GitHubError, GitHubEvidenceCollector, Transport, _check_summary, _repo_path, _status_summary
from .iron_god_observability import REPOSITORIES
from .required_checks import RequiredCheckResolver
from .iron_god_structured_handoff import _SHA


def observe_required_coverage(
    repository: str, sha: str, number: int, transport: Transport,
) -> dict[str, Any]:
    """Observe exact-head required checks using the existing repository policy resolver.

    A point-in-time observation cannot establish product acceptance or permission.
    """
    if (repository not in REPOSITORIES or not isinstance(sha, str)
            or not _SHA.fullmatch(sha) or sha == "0" * 40
            or type(number) is not int or not 1 <= number <= 2**31 - 1):
        raise ValueError("INVALID_COVERAGE_SCOPE")
    result: dict[str, Any] = {
        "repository": repository, "commit_sha": sha, "pull_request": number,
        "status": "UNVERIFIED", "required_checks_state": "UNKNOWN",
        "required_checks": [], "required_coverage_verified": False,
        "can_execute": False,
    }
    root = _repo_path(repository)
    endpoint = f"{root}/pulls/{number}"

    def base_ref(detail: Any) -> str | None:
        if not isinstance(detail, dict) or type(detail.get("number")) is not int:
            return None
        head, base = detail.get("head"), detail.get("base")
        if (detail["number"] != number or detail.get("state") != "open"
                or not isinstance(head, dict) or head.get("sha") != sha
                or not isinstance(base, dict)):
            return None
        for side in (head, base):
            repo = side.get("repo")
            if not isinstance(repo, dict) or repo.get("full_name") != repository:
                return None
        ref = base.get("ref")
        return ref if isinstance(ref, str) and ref and len(ref) <= 255 else None

    try:
        branch = base_ref(transport.get_json(endpoint))
        if branch is None:
            return {**result, "reason": "PULL_REQUEST_SCOPE_MISMATCH"}
        collector = GitHubEvidenceCollector(transport)
        checks = collector._paginate_check_runs(
            f"{root}/commits/{sha}/check-runs?filter=latest", max_pages=3)
        statuses = collector._paginate(
            f"{root}/commits/{sha}/statuses", max_pages=3)
        # Reject malformed or mismatched check runs rather than silently dropping them.
        if any(not isinstance(row, dict) or row.get("head_sha") != sha for row in checks):
            return {**result, "reason": "CHECK_HEAD_MISMATCH"}
        if any(not isinstance(row, dict) for row in statuses):
            return {**result, "reason": "INVALID_STATUS_EVIDENCE"}
        manifest = collector._collect_control_plane(root, branch)
        observed = RequiredCheckResolver(transport).resolve_repository({
            "name": repository, "default_branch": branch, "control_plane": manifest,
            "open_prs": [{"number": number, "base_ref": branch, "head_sha": sha,
                          "check_runs": [_check_summary(row) for row in checks],
                          "status_contexts": [_status_summary(row) for row in statuses]}],
        })["open_prs"][0]
        if base_ref(transport.get_json(endpoint)) != branch:
            return {**result, "reason": "PULL_REQUEST_SCOPE_CHANGED"}
        state = observed["required_checks_state"]
        required = observed["required_checks"]
        # No affirmative coverage claim for an empty policy or inaccessible manifest.
        verified = (state == "RESOLVED" and bool(required)
                    and manifest.get("parse_state") == "PARSED"
                    and observed["required_check_policy"]["manifest_state"] in {"DECLARED", "UNSPECIFIED"}
                    and all(row.get("status") == "completed" and row.get("conclusion") == "success"
                            for row in required))
        return {**result, "status": "OBSERVED", "required_checks_state": state,
                "required_checks": [{key: row.get(key) for key in
                                     ("name", "status", "conclusion", "app_id", "required_app_id")}
                                    for row in required],
                "required_coverage_verified": verified}
    except (GitHubError, OSError, ValueError, TypeError, KeyError, AttributeError):
        return {**result, "reason": "REQUIRED_EVIDENCE_UNAVAILABLE"}
