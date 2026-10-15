from __future__ import annotations

from pathlib import Path

from project_orchestrator.local_storage_pressure import (
    BLOCKED,
    HEALTHY,
    HARD_SAFETY_THRESHOLD_BYTES,
    STORAGE_PRESSURE,
    WARNING_MARGIN_BYTES,
    DiskUsageProbe,
    check_storage_pressure,
)


def _probe(free_bytes: int):
    def fn(path: Path) -> DiskUsageProbe:
        total = 500 * 1024**3
        return DiskUsageProbe(total=total, used=total - free_bytes, free=free_bytes)

    return fn


def test_healthy_well_above_warning_margin(tmp_path):
    report = check_storage_pressure(
        tmp_path, probe=_probe(HARD_SAFETY_THRESHOLD_BYTES + WARNING_MARGIN_BYTES + 1)
    )
    assert report.classification == HEALTHY
    assert report.free_bytes == HARD_SAFETY_THRESHOLD_BYTES + WARNING_MARGIN_BYTES + 1


def test_storage_pressure_exactly_at_hard_threshold(tmp_path):
    report = check_storage_pressure(tmp_path, probe=_probe(HARD_SAFETY_THRESHOLD_BYTES))
    assert report.classification == STORAGE_PRESSURE


def test_storage_pressure_inside_warning_margin(tmp_path):
    report = check_storage_pressure(
        tmp_path, probe=_probe(HARD_SAFETY_THRESHOLD_BYTES + WARNING_MARGIN_BYTES - 1)
    )
    assert report.classification == STORAGE_PRESSURE


def test_blocked_below_hard_threshold(tmp_path):
    report = check_storage_pressure(
        tmp_path, probe=_probe(HARD_SAFETY_THRESHOLD_BYTES - 1)
    )
    assert report.classification == BLOCKED
    assert report.free_bytes == HARD_SAFETY_THRESHOLD_BYTES - 1


def test_blocked_when_free_is_zero(tmp_path):
    report = check_storage_pressure(tmp_path, probe=_probe(0))
    assert report.classification == BLOCKED


def test_missing_path_fails_closed(tmp_path):
    missing = tmp_path / "does-not-exist"
    report = check_storage_pressure(missing, probe=_probe(10**12))
    assert report.classification == BLOCKED
    assert report.free_bytes is None


def test_probe_oserror_fails_closed(tmp_path):
    def raising_probe(path: Path) -> DiskUsageProbe:
        raise OSError("drive unavailable")

    report = check_storage_pressure(tmp_path, probe=raising_probe)
    assert report.classification == BLOCKED
    assert report.free_bytes is None


def test_default_probe_reads_real_disk_usage(tmp_path):
    report = check_storage_pressure(tmp_path)
    assert report.classification in (HEALTHY, STORAGE_PRESSURE, BLOCKED)
    assert report.free_bytes is None or isinstance(report.free_bytes, int)
