"""Deterministic GitHub-native promotion-flow event extraction.

This module converts evidence already returned by the GitHub API into the
versioned exact-head flow record. It deliberately records only events GitHub
can prove. Review/policy/Product Reality/lease readiness remains outside this
collector and therefore UNKNOWN until an authoritative observer records it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .flow_observability import build_flow_record

SUCCESS_CONCLUSIONS = {"success"}


def _iso(value: str | None) -> str | None:
    if not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("GitHub flow timestamps must include a timezone")
    return value


def _completed_at(check: dict[str, Any]) -> str | None:
    if check.get("status") != "completed" or check.get("conclusion") not in SUCCESS_CONCLUSIONS:
        return None
    return _iso(check.get("completed_at"))


def _started_at(check: dict[str, Any]) -> str | None:
    return _iso(check.get("started_at"))


def build_github_flow_record(
    *,
    repository: str,
    pr: dict[str, Any],
    head_checks: list[dict[str, Any]],
    merge_actor: str | None = None,
) -> dict[str, Any]:
    """Build the GitHub-reconstructable portion of one exact-head flow record.

    `latest_head_ci_green_at` is emitted only when every observed exact-head
    check is completed successfully. This collector does not claim those are
    the repository's *required* checks; READY evaluation still owns that gate.
    """
    number = pr.get("number")
    head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
    head_sha = head.get("sha")
    if not isinstance(number, int) or not isinstance(head_sha, str):
        raise ValueError("PR number and exact head SHA are required")

    starts = [value for check in head_checks if (value := _started_at(check))]
    completions = [value for check in head_checks if (value := _completed_at(check))]
    all_green = bool(head_checks) and len(completions) == len(head_checks)

    first_started = min(starts, key=lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))) if starts else None
    green_at = max(completions, key=lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))) if all_green else None

    events = {
        "pr_opened_at": _iso(pr.get("created_at")),
        "first_ci_started_at": first_started,
        "first_ci_green_at": green_at,
        "latest_head_ci_green_at": green_at,
        "merged_at": _iso(pr.get("merged_at")),
    }
    return build_flow_record(
        repository=repository,
        pr_number=number,
        head_sha=head_sha,
        events=events,
        merge_actor=merge_actor,
    )
