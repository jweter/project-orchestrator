"""Allocation gate (roadmap section 32: Definition of Done --
"Storage pressure prevents dangerous new allocation before disk
exhaustion").

Closes a real gap found in the 2026-10-16 Definition-of-Done gap
analysis: check_storage_pressure (local_storage_pressure.py, IG-050)
exists and is correct, but has no production call site anywhere in
the codebase -- nothing actually gates a new worktree, candidate
branch, or other disk-consuming allocation on it. This module
composes check_storage_pressure into that gate. Per the roadmap,
prevention must happen BEFORE disk exhaustion, not after: this gate
blocks new allocation as soon as pressure is STORAGE_PRESSURE (the
warning margin), not only once BLOCKED (the hard floor) is reached
-- waiting for BLOCKED would mean the warning margin was crossed
with no effect, which is exactly what "before disk exhaustion" rules
out. This module performs no allocation itself; it only decides
whether the caller may proceed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .local_storage_pressure import (
    BLOCKED,
    HEALTHY,
    STORAGE_PRESSURE,
    DiskUsageProbeFn,
    StoragePressureReport,
    check_storage_pressure,
)

ALLOCATION_PERMITTED = "ALLOCATION_PERMITTED"
ALLOCATION_BLOCKED = "ALLOCATION_BLOCKED"

KNOWN_CLASSIFICATIONS = frozenset({HEALTHY, STORAGE_PRESSURE, BLOCKED})


class AllocationGateError(RuntimeError):
    """Raised if check_storage_pressure returns an unrecognized
    classification -- never silently treated as permitted."""


@dataclass(frozen=True)
class AllocationDecision:
    status: str
    pressure_report: StoragePressureReport
    reason: str


def decide_allocation(
    path: Path,
    *,
    probe: DiskUsageProbeFn | None = None,
    hard_threshold_bytes: int | None = None,
    warning_margin_bytes: int | None = None,
) -> AllocationDecision:
    """Decide whether a new disk-consuming allocation under `path`'s
    drive may proceed right now.

    Blocks on both STORAGE_PRESSURE and BLOCKED -- only HEALTHY
    permits a new allocation, since "before disk exhaustion" means
    acting on the warning margin, not waiting for the hard floor.
    Optional overrides are forwarded to check_storage_pressure only
    when given, so its own defaults still apply otherwise.
    """
    overrides: dict = {}
    if probe is not None:
        overrides["probe"] = probe
    if hard_threshold_bytes is not None:
        overrides["hard_threshold_bytes"] = hard_threshold_bytes
    if warning_margin_bytes is not None:
        overrides["warning_margin_bytes"] = warning_margin_bytes

    report = check_storage_pressure(path, **overrides)

    if report.classification not in KNOWN_CLASSIFICATIONS:
        raise AllocationGateError(
            f"unrecognized storage pressure classification: {report.classification!r}"
        )

    if report.classification == HEALTHY:
        return AllocationDecision(
            status=ALLOCATION_PERMITTED,
            pressure_report=report,
            reason="storage is HEALTHY; new allocation may proceed",
        )

    return AllocationDecision(
        status=ALLOCATION_BLOCKED,
        pressure_report=report,
        reason=(
            f"storage pressure is {report.classification}; new allocation is "
            "blocked before disk exhaustion, per the roadmap's own "
            "prevention requirement -- STORAGE_PRESSURE alone is enough to "
            "block, never only BLOCKED"
        ),
    )
