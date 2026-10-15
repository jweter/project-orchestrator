from __future__ import annotations

import pytest

from project_orchestrator.iron_god_cache_retention import (
    CacheEntry,
    UnknownCacheKindError,
    UnknownPressureClassificationError,
    decide_cache_retention,
)
from project_orchestrator.local_storage_pressure import (
    BLOCKED,
    HEALTHY,
    STORAGE_PRESSURE,
)


def _entry(**overrides) -> CacheEntry:
    defaults = dict(cache_id="cache-001", kind="pytest_cache", in_active_use=False)
    defaults.update(overrides)
    return CacheEntry(**defaults)


def test_unknown_cache_kind_raises():
    with pytest.raises(UnknownCacheKindError):
        decide_cache_retention(_entry(kind="mystery_cache"), pressure=STORAGE_PRESSURE)


def test_unknown_pressure_raises():
    with pytest.raises(UnknownPressureClassificationError):
        decide_cache_retention(_entry(), pressure="WEIRD")


def test_healthy_never_eligible_even_if_idle():
    decision = decide_cache_retention(_entry(in_active_use=False), pressure=HEALTHY)
    assert decision.eligible_for_pruning is False


@pytest.mark.parametrize("pressure", [STORAGE_PRESSURE, BLOCKED])
def test_idle_cache_eligible_under_pressure(pressure):
    decision = decide_cache_retention(_entry(in_active_use=False), pressure=pressure)
    assert decision.eligible_for_pruning is True


@pytest.mark.parametrize("pressure", [HEALTHY, STORAGE_PRESSURE, BLOCKED])
def test_in_active_use_cache_never_eligible(pressure):
    decision = decide_cache_retention(_entry(in_active_use=True), pressure=pressure)
    assert decision.eligible_for_pruning is False
