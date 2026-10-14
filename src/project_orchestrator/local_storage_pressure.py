"""Read-only storage-pressure monitor for a drive.

This module only ever reads disk usage. It never deletes files, evicts
caches, or mutates the owner-control marker -- classification is advisory
data for a caller to act on, not an action this module takes itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, NamedTuple

HARD_SAFETY_THRESHOLD_BYTES = 25 * 1024**3  # 25 GiB hard floor
WARNING_MARGIN_BYTES = 10 * 1024**3  # warn starting 10 GiB above the floor

HEALTHY = "HEALTHY"
STORAGE_PRESSURE = "STORAGE_PRESSURE"
BLOCKED = "BLOCKED"


class DiskUsageProbe(NamedTuple):
    total: int
    used: int
    free: int


DiskUsageProbeFn = Callable[[Path], DiskUsageProbe]


def _default_probe(path: Path) -> DiskUsageProbe:
    import shutil

    usage = shutil.disk_usage(path)
    return DiskUsageProbe(total=usage.total, used=usage.used, free=usage.free)


@dataclass(frozen=True)
class StoragePressureReport:
    path: Path
    classification: str
    free_bytes: int | None
    reason: str


def check_storage_pressure(
    path: Path,
    *,
    probe: DiskUsageProbeFn = _default_probe,
    hard_threshold_bytes: int = HARD_SAFETY_THRESHOLD_BYTES,
    warning_margin_bytes: int = WARNING_MARGIN_BYTES,
) -> StoragePressureReport:
    """Classify free space on `path`'s drive as HEALTHY, STORAGE_PRESSURE,
    or BLOCKED. A missing path or a probe that raises is reported BLOCKED
    (fail closed) rather than assumed healthy."""
    if not path.exists():
        return StoragePressureReport(
            path=path,
            classification=BLOCKED,
            free_bytes=None,
            reason="path does not exist; failing closed",
        )
    try:
        usage = probe(path)
    except OSError as exc:
        return StoragePressureReport(
            path=path,
            classification=BLOCKED,
            free_bytes=None,
            reason=f"drive inaccessible; failing closed: {exc}",
        )
    if usage.free < hard_threshold_bytes:
        return StoragePressureReport(
            path=path,
            classification=BLOCKED,
            free_bytes=usage.free,
            reason=(
                f"free space {usage.free} bytes is below the hard "
                f"{hard_threshold_bytes}-byte safety threshold"
            ),
        )
    if usage.free < hard_threshold_bytes + warning_margin_bytes:
        return StoragePressureReport(
            path=path,
            classification=STORAGE_PRESSURE,
            free_bytes=usage.free,
            reason=(
                f"free space {usage.free} bytes is within the "
                f"{warning_margin_bytes}-byte warning margin above the hard threshold"
            ),
        )
    return StoragePressureReport(
        path=path,
        classification=HEALTHY,
        free_bytes=usage.free,
        reason=f"free space {usage.free} bytes exceeds the warning margin",
    )
