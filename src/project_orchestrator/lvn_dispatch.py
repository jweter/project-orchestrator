from __future__ import annotations

import hashlib
import json
import re
from typing import Any


class LvnDispatchError(ValueError):
    """Raised when Local Verification Node dispatch evidence is malformed."""


LVN_REQUIRED_MARKER = "<!-- lvn:required -->"
LVN_REQUIRED_LABELS = {
    "local-verification-required",
    "lvn-required",
    "windows-verification-required",
}
_FAILURE_CONCLUSIONS = {
    "ACTION_REQUIRED",
    "CANCELLED",
    "FAILURE",
    "STARTUP_FAILURE",
    "TIMED_OUT",
}
_GREEN_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
_PENDING_STATUSES = {"EXPECTED", "IN_PROGRESS", "PENDING", "QUEUED", "REQUESTED", "WAITING"}
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LvnDispatchError(f"{name} must be a non-empty string")
    return value.strip()


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise LvnDispatchError(f"{name} must be a positive integer")
    return value


def _nonnegative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LvnDispatchError(f"{name} must be a non-negative integer")
    return value


def _sha(value: Any, name: str) -> str:
    text = _nonempty(value, name)
    if not _SHA_RE.fullmatch(text):
        raise LvnDispatchError(f"{name} must be a 40-character lowercase hex SHA")
    return text


def _bool(value: Any, name: str, *, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise LvnDispatchError(f"{name} must be boolean")
    return value


def _labels(raw: Any) -> set[str]:
    if raw is None:
        return set()
    if not isinstance(raw, list):
        raise LvnDispatchError("labels must be a list")
    labels: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            label = item.strip()
        elif isinstance(item, dict):
            name = item.get("name")
            label = name.strip() if isinstance(name, str) else ""
        else:
            raise LvnDispatchError("label entries must be strings or objects with name")
        if not label:
            raise LvnDispatchError("label entries must have a non-empty name")
        labels.add(label.lower())
    return labels


def classify_ci_state(checks: Any) -> str:
    """Reduce authoritative PR checks to FAILED, PENDING, or GREEN.

    Missing checks and unknown states fail closed as PENDING. Callers should pass
    only checks that belong to the repository's authoritative promotion rollup.
    """

    if not isinstance(checks, list) or not checks:
        return "PENDING"
    relevant = []
    for index, check in enumerate(checks):
        if not isinstance(check, dict):
            raise LvnDispatchError(f"checks[{index}] must be an object")
        if check.get("required") is False:
            continue
        relevant.append(check)
    if not relevant:
        return "PENDING"

    saw_pending = False
    for index, check in enumerate(relevant):
        status = str(check.get("status") or "").strip().upper()
        conclusion = str(check.get("conclusion") or "").strip().upper()
        if status in _PENDING_STATUSES or status not in {"COMPLETED", "COMPLETE"}:
            saw_pending = True
            continue
        if conclusion in _FAILURE_CONCLUSIONS:
            return "FAILED"
        if conclusion not in _GREEN_CONCLUSIONS:
            saw_pending = True
    return "PENDING" if saw_pending else "GREEN"


def _requires_lvn(candidate: dict[str, Any]) -> bool:
    body = candidate.get("body")
    if body is not None and not isinstance(body, str):
        raise LvnDispatchError("body must be a string when provided")
    labels = _labels(candidate.get("labels"))
    return LVN_REQUIRED_MARKER in (body or "") or bool(labels & LVN_REQUIRED_LABELS)


def _verification_key(repository: str, pr_number: int, head_sha: str) -> str:
    return f"{repository}#{pr_number}@{head_sha}"


def _normalize_key_set(raw: Any, name: str) -> set[str]:
    if raw is None:
        return set()
    if not isinstance(raw, list):
        raise LvnDispatchError(f"{name} must be a list")
    values: set[str] = set()
    for item in raw:
        values.add(_nonempty(item, name))
    return values


def _normalize_candidate(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise LvnDispatchError("candidate must be an object")
    repository = _nonempty(raw.get("repository"), "candidate.repository")
    head_repository = _nonempty(raw.get("head_repository", repository), "candidate.head_repository")
    pr_number = _positive_int(raw.get("pr_number"), "candidate.pr_number")
    head_sha = _sha(raw.get("head_sha"), "candidate.head_sha")
    branch = _nonempty(raw.get("branch"), "candidate.branch")
    priority = _nonnegative_int(raw.get("priority", 5), "candidate.priority")
    if priority > 6:
        raise LvnDispatchError("candidate.priority must be between 0 and 6")
    return {
        "repository": repository,
        "head_repository": head_repository,
        "pr_number": pr_number,
        "head_sha": head_sha,
        "branch": branch,
        "priority": priority,
        "open": _bool(raw.get("open"), "candidate.open", default=True),
        "draft": _bool(raw.get("draft"), "candidate.draft", default=False),
        "body": raw.get("body") or "",
        "labels": raw.get("labels") or [],
        "checks": raw.get("checks") or [],
    }


def select_lvn_candidates(
    *,
    candidates: list[dict[str, Any]],
    verified_keys: list[str] | None = None,
    deferred_keys: list[str] | None = None,
    allowed_repositories: list[str] | None = None,
    max_dispatch: int = 1,
) -> dict[str, Any]:
    """Select bounded exact-head work for the Local Verification Node.

    Selection order is deterministic:
    1. explicit LVN-required candidates whose authoritative CI is GREEN;
    2. failed-PR reproduction candidates;
    3. nothing speculative.

    Exact PR+SHA evidence is deduplicated. A changed head is a new experiment and
    therefore requires fresh verification.
    """

    if not isinstance(candidates, list):
        raise LvnDispatchError("candidates must be a list")
    max_dispatch = _positive_int(max_dispatch, "max_dispatch")
    verified = _normalize_key_set(verified_keys, "verified_keys")
    deferred = _normalize_key_set(deferred_keys, "deferred_keys")
    allowed = None
    if allowed_repositories is not None:
        if not isinstance(allowed_repositories, list):
            raise LvnDispatchError("allowed_repositories must be a list")
        allowed = {_nonempty(item, "allowed_repositories") for item in allowed_repositories}

    eligible: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for raw in candidates:
        candidate = _normalize_candidate(raw)
        repository = candidate["repository"]
        pr_number = candidate["pr_number"]
        head_sha = candidate["head_sha"]
        key = _verification_key(repository, pr_number, head_sha)
        ci_state = classify_ci_state(candidate["checks"])
        explicit = _requires_lvn(candidate)
        state = "INELIGIBLE"
        reason = "not_selected"
        lane = None

        if allowed is not None and repository not in allowed:
            reason = "repository_not_authorized"
        elif not candidate["open"]:
            reason = "pull_request_closed"
        elif candidate["draft"]:
            reason = "pull_request_is_draft"
        elif candidate["head_repository"] != repository:
            reason = "cross_repository_head_not_authorized"
        elif key in verified:
            state = "SATISFIED"
            reason = "exact_head_already_verified"
        elif key in deferred:
            state = "DEFERRED"
            reason = "exact_head_in_retry_backoff"
        elif explicit and ci_state == "GREEN":
            state = "ELIGIBLE"
            reason = "explicit_promotion_gate_requires_lvn"
            lane = "PROMOTION_GATE"
        elif explicit and ci_state == "PENDING":
            state = "WAIT_CI"
            reason = "promotion_candidate_ci_not_complete"
        elif ci_state == "FAILED":
            state = "ELIGIBLE"
            reason = "failed_pr_reproduction"
            lane = "FAILED_PR_REPRODUCTION"
        elif ci_state == "PENDING":
            state = "WAIT_CI"
            reason = "ci_not_complete"
        else:
            reason = "green_pr_without_explicit_lvn_contract"

        decision = {
            "repository": repository,
            "pr_number": pr_number,
            "head_sha": head_sha,
            "branch": candidate["branch"],
            "verification_key": key,
            "ci_state": ci_state,
            "lvn_required": explicit,
            "state": state,
            "reason": reason,
            "lane": lane,
            "priority": candidate["priority"],
        }
        decisions.append(decision)
        if state == "ELIGIBLE":
            eligible.append(decision)

    lane_rank = {"PROMOTION_GATE": 0, "FAILED_PR_REPRODUCTION": 1}
    eligible.sort(
        key=lambda item: (
            item["priority"],
            lane_rank[item["lane"]],
            item["repository"],
            item["pr_number"],
            item["head_sha"],
        )
    )
    selected = eligible[:max_dispatch]
    selected_keys = {item["verification_key"] for item in selected}
    for decision in decisions:
        if decision["state"] == "ELIGIBLE" and decision["verification_key"] not in selected_keys:
            decision["state"] = "DEFERRED"
            decision["reason"] = "lvn_capacity_exhausted"

    return {
        "schema_version": 1,
        "state": "DISPATCH_READY" if selected else "NO_DISPATCH",
        "selected": selected,
        "decisions": sorted(
            decisions,
            key=lambda item: (item["repository"], item["pr_number"], item["head_sha"]),
        ),
        "capacity": {"max_dispatch": max_dispatch, "exclusive_resource": "LVN_WINDOWS"},
        "selection_authority": "READ_ONLY_EXACT_HEAD_VERIFICATION_ONLY",
    }


def build_lvn_request(
    *,
    selected: dict[str, Any],
    requested_checks: list[str] | None = None,
    environment_class: str = "windows-lvn",
) -> dict[str, Any]:
    """Turn one selected queue item into a deterministic LVN request envelope."""

    if not isinstance(selected, dict):
        raise LvnDispatchError("selected must be an object")
    repository = _nonempty(selected.get("repository"), "selected.repository")
    pr_number = _positive_int(selected.get("pr_number"), "selected.pr_number")
    head_sha = _sha(selected.get("head_sha"), "selected.head_sha")
    branch = _nonempty(selected.get("branch"), "selected.branch")
    lane = _nonempty(selected.get("lane"), "selected.lane")
    if lane not in {"PROMOTION_GATE", "FAILED_PR_REPRODUCTION"}:
        raise LvnDispatchError("selected.lane is invalid")
    checks = requested_checks or ["canonical-preflight"]
    if not isinstance(checks, list) or not checks:
        raise LvnDispatchError("requested_checks must be a non-empty list")
    normalized_checks = sorted({_nonempty(item, "requested_checks") for item in checks})
    environment = _nonempty(environment_class, "environment_class")
    identity = {
        "repository": repository,
        "pr_number": pr_number,
        "branch": branch,
        "head_sha": head_sha,
        "selection_reason": lane,
        "requested_checks": normalized_checks,
        "environment_class": environment,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    request_id = f"lvn-{hashlib.sha256(encoded).hexdigest()[:20]}"
    return {
        "schema_version": 1,
        "request_id": request_id,
        **identity,
        "read_only": True,
        "exact_head_required": True,
        "result_states": [
            "PASS",
            "FAIL",
            "REVIEW_REQUIRED",
            "PRODUCT_REALITY_REQUIRED",
            "ENVIRONMENT_FAILURE",
        ],
        "promotion_authority": "NONE",
    }
