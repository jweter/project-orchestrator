"""Rank *already authorized* systemic engine upgrades for optional Claude windows.

This is an advisory selector, not a cloud dispatch grant. It does not read the
clock, allocate tokens, or change owner control. Ordinary product slices cannot
be selected merely because the capable queue has capacity.
"""

from __future__ import annotations

from typing import Any

MAX_SCORE = 100


def _bounded(value: Any, *, minimum: int = 0, maximum: int = MAX_SCORE) -> int | None:
    if type(value) is not int or not minimum <= value <= maximum:
        return None
    return value


def strategic_upgrade_candidates(
    requests: list[dict[str, Any]], *, cloud_authorized: bool,
    remaining_tokens: int, window_open: bool,
) -> list[dict[str, Any]]:
    """Return ranked eligible upgrades without dispatching or granting authority.

    Candidates must carry an explicit owner-approved strategic classification,
    measurable impact inputs, a bounded estimate and a unique task ID. Failing
    closed keeps a routine backlog item from consuming a scarce Claude window.
    """
    if not cloud_authorized or not window_open or type(remaining_tokens) is not int or remaining_tokens <= 0:
        return []
    ranked: list[tuple[float, str, dict[str, Any]]] = []
    seen: set[str] = set()
    for request in requests:
        if not isinstance(request, dict):
            continue
        ident = request.get("id")
        if not isinstance(ident, str) or not ident or ident in seen:
            continue
        seen.add(ident)
        if request.get("work_class") != "ENGINE_UPGRADE" or request.get("owner_approved") is not True:
            continue
        if request.get("route", {}).get("builder") != "CAPABLE":
            continue
        impact = _bounded(request.get("impact"))
        confidence = _bounded(request.get("confidence"))
        cost = _bounded(request.get("implementation_cost"), minimum=1)
        breadth = _bounded(request.get("affected_repositories"), minimum=1, maximum=7)
        estimate = request.get("estimated_cloud_tokens")
        if (None in (impact, confidence, cost, breadth)
                or type(estimate) is not int or estimate <= 0 or estimate > remaining_tokens):
            continue
        # Impact and breadth favor fixes that unblock the entire portfolio.
        # Confidence and cost penalize speculative or unfinishable work.
        score = impact * breadth * confidence / (100 * cost)
        ranked.append((-score, ident, request))
    ranked.sort(key=lambda row: (row[0], row[1]))
    return [request for _, _, request in ranked]
