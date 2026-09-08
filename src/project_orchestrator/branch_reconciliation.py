from __future__ import annotations

from typing import Any

_REQUIRED = {"repository", "default_branch", "default_head_sha", "branch", "branch_head_sha", "ahead_by", "behind_by", "changed_files"}


def _non_negative_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _validate_sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) < 7:
        raise ValueError(f"{field} must be a commit SHA")
    return value


def build_reconciliation_plan(evidence: dict[str, Any]) -> dict[str, Any]:
    """Classify existing branch value and emit a fail-closed modernization plan.

    This function is deliberately decision-only. It never rewrites refs, rebases,
    cherry-picks, deletes branches, opens PRs, or claims verification.
    """
    missing = _REQUIRED - evidence.keys()
    if missing:
        raise ValueError("branch evidence missing fields: " + ", ".join(sorted(missing)))

    repository = evidence["repository"]
    default_branch = evidence["default_branch"]
    branch = evidence["branch"]
    if not all(isinstance(x, str) and x for x in (repository, default_branch, branch)):
        raise ValueError("repository, default_branch, and branch must be non-empty strings")
    if branch == default_branch:
        raise ValueError("reconciliation candidate cannot be the default branch")

    default_head = _validate_sha(evidence["default_head_sha"], "default_head_sha")
    branch_head = _validate_sha(evidence["branch_head_sha"], "branch_head_sha")
    ahead = _non_negative_int(evidence["ahead_by"], "ahead_by")
    behind = _non_negative_int(evidence["behind_by"], "behind_by")

    changed_files = evidence["changed_files"]
    if not isinstance(changed_files, list) or not all(isinstance(p, str) and p for p in changed_files):
        raise ValueError("changed_files must be a list of non-empty paths")

    open_pr = evidence.get("open_pr")
    actively_owned = bool(evidence.get("actively_owned", False))
    roadmap_authorized = evidence.get("roadmap_authorized")
    if roadmap_authorized not in {True, False, None}:
        raise ValueError("roadmap_authorized must be true, false, or null")

    if ahead == 0 and behind == 0:
        state = "ALIGNED_NO_UNIQUE_WORK"
        action = "NO_RECONCILIATION"
    elif ahead == 0 and behind > 0:
        state = "STALE_NO_UNIQUE_WORK"
        action = "BRANCH_HYGIENE_REVIEW"
    elif ahead > 0 and behind == 0:
        state = "CURRENT_UNMERGED"
        action = "VERIFY_AND_PROMOTE"
    else:
        state = "DIVERGED_VALUABLE"
        action = "MODERNIZE_ON_CURRENT_DEFAULT"

    blockers: list[str] = []
    if open_pr:
        blockers.append("branch already has an open PR; continue that PR lane instead of creating competing work")
    if actively_owned:
        blockers.append("branch has active ownership; do not take over without ownership clearance")
    if roadmap_authorized is False:
        blockers.append("unique work is no longer authorized by current repository roadmap")
    if roadmap_authorized is None and ahead > 0:
        blockers.append("roadmap authorization is unverified")
    if evidence.get("product_reality_dependency") and not evidence.get("product_reality_verified", False):
        blockers.append("candidate depends on unverified Product Reality")

    promotable = action == "VERIFY_AND_PROMOTE" and not blockers
    modernization_ready = action == "MODERNIZE_ON_CURRENT_DEFAULT" and not blockers

    steps: list[str] = []
    if modernization_ready:
        steps = [
            f"create a fresh modernization branch from exact default head {default_head}",
            "replay only still-authorized unique changes from the stale branch without rewriting published history",
            "inspect changed paths against current architecture, regression memory, ownership, and compatibility rules",
            "run repository-approved formatters and targeted tests",
            "run full exact-head preflight",
            "open a Ready-for-Review PR only after PREFLIGHT_GREEN",
            "require fresh independent PR CI before merge",
        ]
    elif promotable:
        steps = [
            "inspect regression memory and repository authority for the changed area",
            "run repository-approved formatters and targeted tests",
            "run full exact-head preflight on the existing branch",
            "open or continue a Ready-for-Review PR only after PREFLIGHT_GREEN",
            "require fresh independent PR CI before merge",
        ]
    elif action == "BRANCH_HYGIENE_REVIEW":
        steps = [
            "verify no open PR, active ownership, release/hotfix role, or unique unmerged commit remains",
            "delete only if branch-hygiene safety checks prove it disposable",
        ]

    return {
        "schema_version": 1,
        "repository": repository,
        "default_branch": default_branch,
        "default_head_sha": default_head,
        "branch": branch,
        "branch_head_sha": branch_head,
        "ahead_by": ahead,
        "behind_by": behind,
        "changed_files": sorted(set(changed_files)),
        "state": state,
        "recommended_action": action,
        "blockers": blockers,
        "modernization_ready": modernization_ready,
        "promotable": promotable,
        "steps": steps,
        "promotion_state": "IMPLEMENTING" if modernization_ready else "PREFLIGHT_UNVERIFIED" if promotable else "BLOCKED" if blockers else "NOT_APPLICABLE",
        "safety_boundaries": [
            "never rewrite published history merely to manufacture a PR",
            "never delete a branch with uncertain disposition",
            "never assume stale unique work is still roadmap-authorized",
            "never claim preflight or CI success without exact-head evidence",
            "existing open PR ownership outranks branch modernization",
            "Product Reality blocks only work that depends on that result",
        ],
    }
