"""Conservative Windows physical-memory admission for local AI builders.

The governor only limits NEW dispatches; existing worker processes and owner
controls are never killed or changed. This is separate from the actor's
existing broader Steward policy and cannot grant execution authority.
"""

from __future__ import annotations

from collections.abc import Callable

# Below 3 GiB: don't add expensive workers. Below 6 GiB: one active worker
# maximum. On a 16 GiB integrated-graphics laptop, two active builders are a
# conservative upper bound even if the Python executor allows six threads.
PAUSE_NEW_BELOW_MIB = 3072
SINGLE_LANE_BELOW_MIB = 6144


def memory_admission(
    running_total: int,
    *,
    sample_fn: Callable[[], tuple[int, int]] | None = None,
) -> dict[str, object]:
    """Return a fail-closed *additional* worker budget; never kill active work."""
    if type(running_total) is not int or running_total < 0:
        raise ValueError("running_total must be nonnegative")
    if sample_fn is None:
        from scripts.adaptive_flow_controller import sample_windows_memory

        sample_fn = sample_windows_memory
    try:
        free_mib, total_mib = sample_fn()
        if (
            type(free_mib) is not int
            or type(total_mib) is not int
            or not 0 <= free_mib <= total_mib
            or total_mib <= 0
        ):
            raise ValueError("untrusted memory metrics")
    except (OSError, AttributeError, TypeError, ValueError):
        return {
            "status": "MEMORY_PROBE_UNVERIFIED",
            "new_worker_budget": 0,
            "active_workers": running_total,
            "execution_authorized": False,
        }
    if free_mib < PAUSE_NEW_BELOW_MIB:
        active_limit = 0
        reason = "CRITICAL_MEMORY_NO_NEW_WORK"
    elif free_mib < SINGLE_LANE_BELOW_MIB:
        active_limit = 1
        reason = "LOW_MEMORY_SINGLE_LANE"
    else:
        active_limit = 2
        reason = "MEMORY_WITHIN_BOUNDED_CAPACITY"

    return {
        "status": reason,
        "new_worker_budget": max(0, active_limit - running_total),
        "active_limit": active_limit,
        "active_workers": running_total,
        "available_mib": free_mib,
        "total_mib": total_mib,
        "execution_authorized": False,
    }
