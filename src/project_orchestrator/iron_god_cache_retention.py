"""Cache retention rules (roadmap section 17: Storage Governor).

Given the current storage-pressure classification
(local_storage_pressure.py, IG-050) and a cache entry, decides whether
that cache is eligible for pruning. Decision-only -- this module never
deletes anything itself; an actual pruning action, if ever built, is a
separate, independently-authorized slice (the same decision/action
split as IG-025/IG-051 for worktrees). A cache in active use is never
eligible, regardless of pressure.
"""

from __future__ import annotations

from dataclasses import dataclass

from .local_storage_pressure import BLOCKED, HEALTHY, STORAGE_PRESSURE

KNOWN_CACHE_KINDS = frozenset(
    {"pytest_cache", "ruff_cache", "pip_cache", "aider_cache", "ollama_model_cache"}
)
KNOWN_PRESSURE_CLASSIFICATIONS = frozenset({HEALTHY, STORAGE_PRESSURE, BLOCKED})


class UnknownCacheKindError(RuntimeError):
    """Raised when an unrecognized cache kind is evaluated."""


class UnknownPressureClassificationError(RuntimeError):
    """Raised when an unrecognized storage-pressure classification is given."""


@dataclass(frozen=True)
class CacheEntry:
    cache_id: str
    kind: str
    in_active_use: bool


@dataclass(frozen=True)
class CacheRetentionDecision:
    cache_id: str
    eligible_for_pruning: bool
    reason: str


def decide_cache_retention(
    entry: CacheEntry, *, pressure: str
) -> CacheRetentionDecision:
    """Decide whether `entry` is eligible for pruning under `pressure`.

    HEALTHY: never prune (no pressure to relieve). STORAGE_PRESSURE or
    BLOCKED: eligible only if the cache is not currently in active use
    -- an in-use cache is never "safe," regardless of pressure level.
    """
    if entry.kind not in KNOWN_CACHE_KINDS:
        raise UnknownCacheKindError(f"unknown cache kind: {entry.kind!r}")
    if pressure not in KNOWN_PRESSURE_CLASSIFICATIONS:
        raise UnknownPressureClassificationError(
            f"unknown pressure classification: {pressure!r}"
        )

    if entry.in_active_use:
        return CacheRetentionDecision(
            cache_id=entry.cache_id,
            eligible_for_pruning=False,
            reason="cache is in active use; never eligible regardless of pressure",
        )

    if pressure == HEALTHY:
        return CacheRetentionDecision(
            cache_id=entry.cache_id,
            eligible_for_pruning=False,
            reason="storage is HEALTHY; no pruning pressure",
        )

    return CacheRetentionDecision(
        cache_id=entry.cache_id,
        eligible_for_pruning=True,
        reason=f"storage pressure is {pressure}; cache is idle and eligible",
    )
