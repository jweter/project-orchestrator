"""Pure monkeypatched resource-admission probes: never change the owner gate."""

from types import SimpleNamespace

import pytest

from project_orchestrator import iron_god_canon_lane as canon
from project_orchestrator import worker_memory_admission as memory


@pytest.fixture(autouse=True)
def measured_memory_available(monkeypatch):
    monkeypatch.setattr(memory, "memory_admission", lambda _workers: {
        "new_worker_budget": 1, "status": "ADMITTED", "available_mib": 8192,
    })


@pytest.mark.parametrize("failure", [
    OSError("volume inaccessible"), ValueError("usage invalid"),
])
def test_disk_probe_failure_denies_worker_without_crashing(monkeypatch, failure):
    def unavailable(_path):
        raise failure
    monkeypatch.setattr(canon.shutil, "disk_usage", unavailable)
    result = canon.default_resources()
    assert result == {"admitted": False, "reason": "DISK_PROBE_FAILED",
                      "min_free_disk_gib": canon.MIN_FREE_DISK_GIB}


@pytest.mark.parametrize("free_gib,allowed,reason", [
    (23, False, "LOW_DISK"), (25, True, "ADMITTED"),
])
def test_disk_floor_remains_enforced_and_shortfall_is_measured(
        monkeypatch, free_gib, allowed, reason):
    gib = 1024 ** 3
    monkeypatch.setattr(canon.shutil, "disk_usage", lambda _path: SimpleNamespace(
        free=free_gib * gib, total=500 * gib,
    ))
    result = canon.default_resources()
    assert result["admitted"] is allowed
    assert result["reason"] == reason
    assert result["min_free_disk_gib"] == canon.MIN_FREE_DISK_GIB
    assert result["disk_shortfall_gib"] == max(0, 25 - free_gib)


@pytest.mark.parametrize("free,total", [(-1, 100), (101, 100), (10, 0)])
def test_invalid_disk_capacity_never_grants_admission(monkeypatch, free, total):
    monkeypatch.setattr(canon.shutil, "disk_usage", lambda _path: SimpleNamespace(
        free=free, total=total,
    ))
    assert canon.default_resources()["reason"] == "DISK_PROBE_FAILED"


@pytest.mark.parametrize("budget", ["invalid", None, True, -1])
def test_corrupt_or_noninteger_memory_budget_does_not_admit(monkeypatch, budget):
    gib = 1024 ** 3
    monkeypatch.setattr(memory, "memory_admission", lambda _workers: {
        "new_worker_budget": budget, "status": "UNVERIFIED",
    })
    monkeypatch.setattr(canon.shutil, "disk_usage", lambda _path: SimpleNamespace(
        free=30 * gib, total=500 * gib,
    ))
    assert canon.default_resources()["admitted"] is False
