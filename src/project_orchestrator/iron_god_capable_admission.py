"""Cross-repository admission for the paid capable builder (issue #673, phase 2).

Dispatch used to be attempted repository by repository as each one was refilled, so whichever repositories were
visited first spent the whole 24-hour budget in one burst. Candidates are now collected for the whole pass and
admitted here: deterministic order (independent of repository visiting order), fair across repositories, fresh
requests before retries of a no-PR failure, and never more than a share of the remaining budget in one pass.
Pure functions: no I/O and no dispatch, so every decision is reproducible and inspectable.
"""
from __future__ import annotations

import math
from typing import Any

WINDOW_S = 24 * 3600
NO_PR_NOTE = "STALE_NO_PULL_REQUEST_OBSERVED"


def _request_id(row: dict[str, Any]) -> str:
    return str(row.get("task_key", "")).split("#a")[0]


def remaining_slots(rows: list[dict[str, Any]], daily_limit: int, now: float) -> int:
    used = sum(1 for row in rows if now - float(row.get("started_at", 0)) < WINDOW_S)
    return max(0, int(daily_limit) - used)


def next_slot_at(rows: list[dict[str, Any]], daily_limit: int, now: float) -> float | None:
    """When the rolling window next frees a slot, or None while one is already free."""
    live = sorted(float(row.get("started_at", 0)) for row in rows if now - float(row.get("started_at", 0)) < WINDOW_S)
    if daily_limit <= 0 or len(live) < daily_limit:
        return None
    return live[len(live) - int(daily_limit)] + WINDOW_S


def per_pass_cap(remaining: int) -> int:
    """At most half of what is left per pass (at least one), so a burst cannot take every slot at once."""
    return 0 if remaining <= 0 else max(1, math.ceil(remaining / 2))


def rank_key(candidate: dict[str, Any], rows: list[dict[str, Any]], now: float) -> tuple[Any, ...]:
    request = candidate["request"]
    repo = str(request.get("repository") or candidate.get("name", ""))
    request_id = str(request.get("id", ""))
    mine = [r for r in rows if _request_id(r) == request_id]
    no_pr = sum(1 for r in mine if r.get("notes") == NO_PR_NOTE)
    repo_recent = sum(1 for r in rows if r.get("repository") == repo and now - float(r.get("started_at", 0)) < WINDOW_S)
    # fresh work first, then the repository with the fewest recent slots, then fewest attempts, then a stable id
    return (no_pr, repo_recent, len(mine), repo, request_id)


def sorted_candidates(candidates: list[dict[str, Any]], rows: list[dict[str, Any]] | None = None,
                      now: float = 0.0) -> list[dict[str, Any]]:
    return sorted(candidates, key=lambda c: rank_key(c, rows or [], now))


def plan(candidates: list[dict[str, Any]], rows: list[dict[str, Any]], daily_limit: int, now: float) -> dict[str, Any]:
    """Which candidates to try now (in order) and why every other one waits."""
    remaining = remaining_slots(rows, daily_limit, now)
    cap = per_pass_cap(remaining)
    admit: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    taken: set[str] = set()
    for candidate in sorted_candidates(candidates, rows, now):
        request = candidate["request"]
        repo = str(request.get("repository") or candidate.get("name", ""))
        if remaining <= 0:
            reason = "DAILY_BUDGET_EXHAUSTED"
        elif repo in taken:
            reason = "ONE_PER_REPOSITORY_PER_PASS"
        elif len(admit) >= cap:
            reason = "PASS_SHARE_REACHED"
        else:
            admit.append(candidate)
            taken.add(repo)
            continue
        deferred.append({"candidate": candidate, "reason": reason})
    return {"admit": admit, "deferred": deferred, "remaining": remaining, "pass_cap": cap,
            "next_slot_at": next_slot_at(rows, daily_limit, now)}
