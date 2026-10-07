from __future__ import annotations

from collections import Counter
from typing import Any

ISSUE_STATES = frozenset({
    "NEW", "NEEDS_REPRODUCTION", "CONFIRMED", "DUPLICATE", "ALREADY_FIXED",
    "BLOCKED", "IMPLEMENTING", "PR_OPEN", "CI_FAILED", "MERGED_PENDING_PROOF",
    "CLOSED_VERIFIED",
})
TERMINAL_STATES = frozenset({"DUPLICATE", "ALREADY_FIXED", "CLOSED_VERIFIED"})
ACTIVE_STATES = ISSUE_STATES - TERMINAL_STATES

def normalize_issue(issue: dict[str, Any], *, product: str, repository: str) -> dict[str, Any]:
    number = issue.get("number")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise ValueError("issue.number must be a positive integer")
    title = issue.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("issue.title must be non-empty")
    state = str(issue.get("engineering_state") or "NEW")
    if state not in ISSUE_STATES:
        raise ValueError(f"invalid engineering_state: {state}")
    return {
        "work_id": f"issue:{repository}#{number}",
        "kind": "ISSUE",
        "product": product,
        "repository": repository,
        "issue_number": number,
        "title": title.strip(),
        "url": issue.get("html_url") or issue.get("url"),
        "engineering_state": state,
        "eligible": issue.get("eligible", state in ACTIVE_STATES),
        "blocked": bool(issue.get("blocked", state == "BLOCKED")),
        "owned": bool(issue.get("owned", False)),
        "severity": _bounded(issue.get("severity"), 0, 4),
        "usability_impact": _bounded(issue.get("usability_impact"), 0, 4),
        "roadmap_unlock": _bounded(issue.get("roadmap_unlock"), 0, 4),
        "dependency_unlock": _bounded(issue.get("dependency_unlock"), 0, 4),
        "age_bucket": _bounded(issue.get("age_bucket"), 0, 4),
        "risk": _bounded(issue.get("risk"), 0, 4),
        "effort": _bounded(issue.get("effort"), 0, 4),
        "evidence_refs": _strings(issue.get("evidence_refs", [])),
        "next_gate": str(issue.get("next_gate") or _next_gate(state)),
    }

def issue_priority_key(item: dict[str, Any]) -> tuple[int, int, int, int, int, int, int, str]:
    """Deterministic impact ordering; lower tuple sorts first.

    Existing P0-P4 scheduler authority remains outside this function. This ranks
    issue candidates only after they are legal in their dependency lane.
    """
    return (
        -int(item.get("severity", 0)),
        -int(item.get("usability_impact", 0)),
        -int(item.get("roadmap_unlock", 0)),
        -int(item.get("dependency_unlock", 0)),
        -int(item.get("age_bucket", 0)),
        int(item.get("risk", 0)),
        int(item.get("effort", 0)),
        str(item.get("work_id") or ""),
    )

def rank_actionable_issues(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    legal = [
        item for item in items
        if item.get("kind") == "ISSUE"
        and item.get("engineering_state") in ACTIVE_STATES
        and item.get("eligible") is True
        and item.get("blocked") is not True
        and item.get("owned") is not True
    ]
    return sorted(legal, key=issue_priority_key)

def closure_decision(item: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    """Fail-closed issue closure. Merge alone is not closure evidence."""
    required = {
        "reproduced_or_disposition_proven": evidence.get("reproduced_or_disposition_proven") is True,
        "implementation_verified": evidence.get("implementation_verified") is True,
        "regression_protection": evidence.get("regression_protection") in {"ADDED", "NOT_APPLICABLE_PROVEN"},
        "required_ci_green": evidence.get("required_ci_green") is True,
        "merged_default_branch": evidence.get("merged_default_branch") is True,
        "default_branch_proof": evidence.get("default_branch_proof") is True,
        "product_reality": evidence.get("product_reality") in {"VERIFIED", "NOT_REQUIRED"},
    }
    missing = [name for name, ok in required.items() if not ok]
    return {
        "work_id": item.get("work_id"),
        "close": not missing,
        "engineering_state": "CLOSED_VERIFIED" if not missing else (
            "MERGED_PENDING_PROOF" if evidence.get("merged_default_branch") is True else item.get("engineering_state", "CONFIRMED")
        ),
        "missing_evidence": missing,
        "evidence_refs": _strings(evidence.get("evidence_refs", [])),
    }

def issue_debt_telemetry(items: list[dict[str, Any]]) -> dict[str, Any]:
    states = Counter(str(item.get("engineering_state") or "UNKNOWN") for item in items)
    actionable = sum(1 for item in items if item.get("engineering_state") in ACTIVE_STATES)
    critical = sum(
        1 for item in items
        if item.get("engineering_state") in ACTIVE_STATES
        and (int(item.get("severity", 0)) >= 3 or int(item.get("usability_impact", 0)) >= 3)
    )
    return {
        "open_total": actionable,
        "actionable": sum(
            1 for item in items
            if item.get("engineering_state") in ACTIVE_STATES
            and item.get("eligible") is True and item.get("blocked") is not True
        ),
        "usability_or_severity_critical": critical,
        "states": dict(sorted(states.items())),
        "orphaned_without_next_gate": sum(
            1 for item in items
            if item.get("engineering_state") in ACTIVE_STATES and not item.get("next_gate")
        ),
    }

def assert_no_orphaned_actionable_issues(items: list[dict[str, Any]]) -> None:
    orphaned = [
        str(item.get("work_id")) for item in items
        if item.get("engineering_state") in ACTIVE_STATES
        and (not item.get("next_gate") or item.get("eligible") is None)
    ]
    if orphaned:
        raise ValueError("orphaned actionable issues: " + ", ".join(sorted(orphaned)))

def _next_gate(state: str) -> str:
    return {
        "NEW": "TRIAGE",
        "NEEDS_REPRODUCTION": "REPRODUCE",
        "CONFIRMED": "IMPLEMENT",
        "BLOCKED": "UNBLOCK",
        "IMPLEMENTING": "VERIFY",
        "PR_OPEN": "CI",
        "CI_FAILED": "REPAIR",
        "MERGED_PENDING_PROOF": "DEFAULT_BRANCH_PROOF",
    }.get(state, "NONE")

def _bounded(value: Any, low: int, high: int) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        raise ValueError("score must be an integer")
    parsed = int(value)
    if parsed < low or parsed > high:
        raise ValueError(f"score must be between {low} and {high}")
    return parsed

def _strings(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(x, str) and x.strip() for x in value):
        raise ValueError("evidence_refs must be a list of non-empty strings")
    return [x.strip() for x in value]
