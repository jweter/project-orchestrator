from __future__ import annotations

from pathlib import Path

from project_orchestrator.iron_god_allocation_gate import (
    ALLOCATION_BLOCKED,
    ALLOCATION_PERMITTED,
    decide_allocation,
)
from project_orchestrator.local_storage_pressure import DiskUsageProbe


def _probe(total: int, used: int, free: int):
    def fn(path: Path) -> DiskUsageProbe:
        return DiskUsageProbe(total=total, used=used, free=free)

    return fn


def test_healthy_permits_allocation(tmp_path):
    result = decide_allocation(tmp_path, probe=_probe(100, 10, 90 * 1024**3))
    assert result.status == ALLOCATION_PERMITTED
    assert result.pressure_report.classification == "HEALTHY"


def test_storage_pressure_blocks_allocation_before_hard_floor(tmp_path):
    # 30 GiB free: above the 25 GiB hard floor but within the 10 GiB
    # warning margin -- STORAGE_PRESSURE, not BLOCKED.
    result = decide_allocation(tmp_path, probe=_probe(100, 10, 30 * 1024**3))
    assert result.pressure_report.classification == "STORAGE_PRESSURE"
    assert result.status == ALLOCATION_BLOCKED


def test_blocked_blocks_allocation(tmp_path):
    result = decide_allocation(tmp_path, probe=_probe(100, 10, 1 * 1024**3))
    assert result.pressure_report.classification == "BLOCKED"
    assert result.status == ALLOCATION_BLOCKED


def test_missing_path_blocks_allocation():
    result = decide_allocation(Path("Z:/definitely/does/not/exist"))
    assert result.status == ALLOCATION_BLOCKED
    assert result.pressure_report.classification == "BLOCKED"


def test_probe_raising_oserror_blocks_allocation(tmp_path):
    def failing_probe(path: Path) -> DiskUsageProbe:
        raise OSError("disk unavailable")

    result = decide_allocation(tmp_path, probe=failing_probe)
    assert result.status == ALLOCATION_BLOCKED
    assert result.pressure_report.classification == "BLOCKED"


def test_custom_thresholds_are_passed_through(tmp_path):
    # With a 1-byte hard threshold and 0 warning margin, even 1 free
    # byte should be HEALTHY -- proves overrides reach check_storage_pressure.
    result = decide_allocation(
        tmp_path,
        probe=_probe(100, 99, 1),
        hard_threshold_bytes=1,
        warning_margin_bytes=0,
    )
    assert result.status == ALLOCATION_PERMITTED
    assert result.pressure_report.classification == "HEALTHY"
