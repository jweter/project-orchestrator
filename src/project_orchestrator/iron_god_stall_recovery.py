"""Deterministic local-first recovery decisions for stalled roadmap requests.

Advisory only: the existing owner controller, lease pool, backlog admission,
independent acceptance, and canonical preflight remain the execution gates.
No cloud transport is selected by this module.
"""
from __future__ import annotations

from typing import Any

TRANSIENT = frozenset({"WORKER_INTERRUPTED", "LEASE_EXPIRED", "TRANSIENT_IO", "MODEL_TIMEOUT"})
MISSING_ORACLE = frozenset({"NEEDS_ACCEPTANCE_EXAMPLES", "NEEDS_INDEPENDENT_ACCEPTANCE_EVIDENCE"})
MAX_RETRIES = 2


def recovery_decision(
    request: dict[str, Any], *, reason: str, attempts: int,
    eligible_alternatives: list[dict[str, Any]], owner_authorized: bool,
    memory_available: bool,
) -> dict[str, Any]:
    """Choose a safe *next step*, not permission to execute it.

    Repeated identical failures never trigger unbounded retries. Alternative
    tasks are already independently eligible according to the caller; this
    selector does not synthesize roadmap authority or acceptance examples.
    """
    ident = request.get("id")
    if not isinstance(ident, str) or not ident:
        return {"action": "BLOCK", "reason": "INVALID_REQUEST"}
    if not owner_authorized:
        return {"action": "PAUSE", "reason": "OWNER_CONTROL"}
    if not memory_available:
        return {"action": "WAIT", "reason": "MEMORY_FLOOR"}
    if type(attempts) is not int or attempts < 0:
        return {"action": "BLOCK", "reason": "INVALID_ATTEMPT_COUNT"}
    if reason in TRANSIENT and attempts < MAX_RETRIES:
        return {"action": "RETRY_LOCAL", "request_id": ident, "reason": reason}
    # Only existing capability-level 3+ requests can enter the existing
    # decomposition lane; its validator still enforces full acceptance coverage.
    if (isinstance(request.get("route"), dict) and request["route"].get("builder") == "CAPABLE"
            and type(request.get("capability_level")) is int
            and request["capability_level"] >= 3
            and attempts < MAX_RETRIES
            and reason not in MISSING_ORACLE):
        return {"action": "DECOMPOSE_LOCAL", "request_id": ident, "reason": reason}
    for candidate in eligible_alternatives:
        if (isinstance(candidate, dict) and isinstance(candidate.get("id"), str)
                and candidate["id"] != ident and candidate.get("eligible") is True
                and candidate.get("owner_approved") is True):
            return {"action": "CONTINUE_OTHER_LOCAL", "request_id": candidate["id"],
                    "blocked_request_id": ident, "reason": reason}
    return {"action": "BLOCK", "request_id": ident, "reason": reason or "NO_SAFE_LOCAL_PATH"}
