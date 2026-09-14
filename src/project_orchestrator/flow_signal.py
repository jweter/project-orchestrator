from __future__ import annotations

from typing import Any

from .throughput_guard import classify_promotion_failure


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _sha(value: Any, field: str) -> str:
    text = _nonempty(value, field)
    if len(text) != 40 or any(ch not in "0123456789abcdef" for ch in text):
        raise ValueError(f"{field} must be a 40-character lowercase hex SHA")
    return text


def classify_workflow_outcome(evidence: dict[str, Any]) -> dict[str, Any]:
    """Separate product defects from expected safety refusals and platform gates.

    This is an operational classification layer. It never converts stale or
    unknown verification into GREEN and never weakens exact-head promotion.
    """
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be an object")

    applicable = evidence.get("applicable")
    if applicable not in {True, False, None}:
        raise ValueError("applicable must be true, false, or null")
    if applicable is False:
        return {
            "schema_version": 1,
            "operational_state": "NOT_APPLICABLE",
            "dashboard_severity": "NEUTRAL",
            "counts_as_product_failure": False,
            "requires_code_repair": False,
            "fail_closed": False,
            "next_action": "NO_ACTION",
            "failure_class": None,
        }

    expected = evidence.get("expected_head_sha")
    actual = evidence.get("actual_head_sha")
    if expected is not None or actual is not None:
        expected_sha = _sha(expected, "expected_head_sha")
        actual_sha = _sha(actual, "actual_head_sha")
        if expected_sha != actual_sha:
            return {
                "schema_version": 1,
                "operational_state": "PROMOTION_STALE",
                "dashboard_severity": "YELLOW",
                "counts_as_product_failure": False,
                "requires_code_repair": False,
                "fail_closed": True,
                "next_action": "REVALIDATE_CURRENT_HEAD",
                "failure_class": None,
                "expected_head_sha": expected_sha,
                "actual_head_sha": actual_sha,
            }

    status = evidence.get("workflow_status")
    conclusion = evidence.get("workflow_conclusion")
    if status in {"queued", "in_progress", "waiting", "pending"} or conclusion is None:
        return {
            "schema_version": 1,
            "operational_state": "PENDING",
            "dashboard_severity": "YELLOW",
            "counts_as_product_failure": False,
            "requires_code_repair": False,
            "fail_closed": True,
            "next_action": "WAIT_FOR_TERMINAL_EVIDENCE",
            "failure_class": None,
        }
    if conclusion == "success":
        return {
            "schema_version": 1,
            "operational_state": "VERIFIED_GREEN",
            "dashboard_severity": "GREEN",
            "counts_as_product_failure": False,
            "requires_code_repair": False,
            "fail_closed": False,
            "next_action": "CONTINUE_PROMOTION_REVALIDATION",
            "failure_class": None,
        }

    classification = classify_promotion_failure(evidence)
    failure_class = classification["failure_class"]
    platform = failure_class == "PLATFORM_GATE"
    return {
        "schema_version": 1,
        "operational_state": "PLATFORM_GATE" if platform else "ACTIONABLE_FAILURE",
        "dashboard_severity": "RED",
        "counts_as_product_failure": not platform,
        "requires_code_repair": failure_class in {"CODE_FAILURE", "TEST_FAILURE", "QUALITY_FAILURE"},
        "fail_closed": True,
        "next_action": "REPAIR_OR_ROUTE_PLATFORM_GATE" if platform else "DIAGNOSE_AND_REPAIR",
        "failure_class": failure_class,
        "basis": classification["basis"],
    }


def build_stale_promotion_recovery(evidence: dict[str, Any]) -> dict[str, Any]:
    """Plan safe recovery after a promotion candidate's branch head moves."""
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be an object")
    repository = _nonempty(evidence.get("repository"), "repository")
    branch = _nonempty(evidence.get("branch"), "branch")
    expected = _sha(evidence.get("expected_head_sha"), "expected_head_sha")
    current = _sha(evidence.get("current_head_sha"), "current_head_sha")
    verified = evidence.get("verified_head_sha")
    verified_sha = _sha(verified, "verified_head_sha") if verified is not None else None

    if current == expected and verified_sha == current:
        state = "EXACT_HEAD_STILL_VALID"
        next_action = "CONTINUE_CENTRAL_PROMOTION_REVALIDATION"
        preflight_reuse_allowed = True
    elif verified_sha == current:
        state = "HEAD_MOVED_BUT_CURRENT_HEAD_ALREADY_VERIFIED"
        next_action = "REVALIDATE_CI_REVIEWS_AND_MERGEABILITY"
        preflight_reuse_allowed = True
    else:
        state = "REVERIFY_REQUIRED"
        next_action = "RUN_FULL_EXACT_HEAD_PREFLIGHT"
        preflight_reuse_allowed = False

    return {
        "schema_version": 1,
        "repository": repository,
        "branch": branch,
        "expected_head_sha": expected,
        "current_head_sha": current,
        "verified_head_sha": verified_sha,
        "state": state,
        "preflight_reuse_allowed": preflight_reuse_allowed,
        "merge_allowed": False,
        "next_action": next_action,
        "safety_rule": "head movement invalidates promotion until exact-head evidence is re-established",
    }


def build_reconciliation_pr_plan(evidence: dict[str, Any]) -> dict[str, Any]:
    """Route bot-generated reconciliation branches to central PR authority.

    Product workflows may push an authorized branch, but the orchestrator's
    GitHub connection owns PR creation. This avoids granting broad pull-request
    creation rights to GITHUB_TOKEN and avoids recursion-suppression surprises.
    """
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be an object")
    repository = _nonempty(evidence.get("repository"), "repository")
    branch = _nonempty(evidence.get("branch"), "branch")
    head_sha = _sha(evidence.get("head_sha"), "head_sha")
    source_actor = _nonempty(evidence.get("source_actor"), "source_actor")
    prefixes = evidence.get("authorized_branch_prefixes")
    if not isinstance(prefixes, list) or not prefixes or not all(isinstance(p, str) and p for p in prefixes):
        raise ValueError("authorized_branch_prefixes must be a non-empty string list")
    open_pr_number = evidence.get("open_pr_number")
    if open_pr_number is not None and (
        isinstance(open_pr_number, bool) or not isinstance(open_pr_number, int) or open_pr_number < 1
    ):
        raise ValueError("open_pr_number must be a positive integer or null")
    branch_pushed = evidence.get("branch_pushed")
    if not isinstance(branch_pushed, bool):
        raise ValueError("branch_pushed must be boolean")

    authorized = any(branch.startswith(prefix) for prefix in prefixes)
    if not authorized:
        action = "BLOCKED_UNAUTHORIZED_BRANCH"
    elif not branch_pushed:
        action = "WAIT_FOR_BRANCH_PUSH"
    elif open_pr_number is not None:
        action = "CONTINUE_EXISTING_PR"
    elif source_actor not in {"github-actions[bot]", "github-actions"}:
        action = "CENTRAL_REVIEW_REQUIRED"
    else:
        action = "CREATE_PR_VIA_ORCHESTRATOR_CONNECTION"

    return {
        "schema_version": 1,
        "repository": repository,
        "branch": branch,
        "head_sha": head_sha,
        "source_actor": source_actor,
        "authorized_branch": authorized,
        "open_pr_number": open_pr_number,
        "action": action,
        "pr_creation_authority": "PORTFOLIO_ORCHESTRATOR_GITHUB_CONNECTION",
        "github_actions_may_create_pr": False,
        "merge_allowed": False,
        "next_gate": "fresh PR CI and normal review gates after central PR creation"
        if action == "CREATE_PR_VIA_ORCHESTRATOR_CONNECTION"
        else "resolve the routing state before PR creation",
    }


def compute_diagnostic_churn(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Measure avoidable diagnostic investigations from explicit run evidence only."""
    if not isinstance(records, list):
        raise ValueError("records must be a list")
    investigations = 0
    avoidable = 0
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("record entries must be objects")
        investigated = record.get("diagnostic_investigation")
        known = record.get("known_non_actionable_before_investigation")
        if investigated is None and known is None:
            continue
        if not isinstance(investigated, bool) or not isinstance(known, bool):
            raise ValueError(
                "diagnostic_investigation and known_non_actionable_before_investigation must be booleans"
            )
        if investigated:
            investigations += 1
            if known:
                avoidable += 1

    if investigations == 0:
        return {
            "schema_version": 1,
            "avoidable_diagnostic_churn_count": {
                "status": "UNKNOWN",
                "value": None,
                "unit": "count",
                "sample_count": 0,
                "reason": "no explicit diagnostic-investigation evidence",
            },
            "avoidable_diagnostic_churn_rate": {
                "status": "UNKNOWN",
                "value": None,
                "unit": "ratio",
                "sample_count": 0,
                "reason": "no explicit diagnostic-investigation evidence",
            },
        }

    return {
        "schema_version": 1,
        "avoidable_diagnostic_churn_count": {
            "status": "MEASURED",
            "value": avoidable,
            "unit": "count",
            "sample_count": investigations,
            "reason": "",
        },
        "avoidable_diagnostic_churn_rate": {
            "status": "MEASURED",
            "value": avoidable / investigations,
            "unit": "ratio",
            "sample_count": investigations,
            "reason": "",
        },
    }
