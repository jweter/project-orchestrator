"""Capability-aware model routing: the right local model for a task, from measured evidence and current memory.

The rule that cannot be broken: **a task is only ever routed to a model whose measured profile covers the task's
capability level.** Memory pressure can make the engine wait or use a smaller model for work that model has demonstrated;
it can never make it quietly hand a hard task to a model that has not shown it can do it.

Policy, in order:

1. Qualified models are those with a valid profile (right model, hardware and ladder version) whose measured
   ``max_passing_level`` is at least the task's level.
2. Of the qualified models, the first in ``LADDER`` order (the primary builder first) that memory admits *right now* wins.
3. If a qualified model exists but none is admitted, the answer is to wait, with the exact numbers. If none qualifies, the
   answer names the missing calibration or the level gap. Nothing is ever downgraded to fit.

Nothing in this module loads a model or touches the machine: callers pass the profiles and the free memory, which keeps
the decision deterministic and testable. The memory thresholds come from the shared door (``iron_god_local_model``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import iron_god_local_model as lm
from .iron_god_calibration import capability_gate

# Preference order: the primary builder first, then the smaller models. A model that is not in this list is never routed to.
LADDER: tuple[str, ...] = ("qwen2.5-coder:7b", "qwen3.5:4b", "qwen2.5-coder:3b")


@dataclass(frozen=True)
class Route:
    model: str | None  # bare model name to run, or None: wait (see ``reason``)
    reason: str
    level: int | None
    qualified: tuple[str, ...] = ()
    admitted: tuple[str, ...] = ()
    required_gib: float | None = None
    available_gib: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"model": self.model, "reason": self.reason, "level": self.level, "qualified": list(self.qualified),
                "admitted_now": list(self.admitted), "required_gib": self.required_gib, "available_gib": self.available_gib}


def _full(name: str) -> str:
    return f"ollama/{name}"


def _valid(profiles: dict[str, dict[str, Any]], name: str, level: int, hardware: dict[str, Any] | None) -> str | None:
    """None when ``name`` has a valid measured profile covering ``level``; otherwise the fail-closed reason."""
    return capability_gate(profiles.get(_full(name)), {"capability_level": level}, model=_full(name), hardware=hardware)


def qualified_models(level: int, profiles: dict[str, dict[str, Any]], *, hardware: dict[str, Any] | None = None,
                     ladder: tuple[str, ...] = LADDER) -> list[str]:
    return [name for name in ladder if _valid(profiles, name, level, hardware) is None]


def required_gib(name: str) -> float:
    return lm.gate_for(name, available=lambda: 0.0).required_gib


def route(level: Any, profiles: dict[str, dict[str, Any]], available_gib: float | None, *,
          hardware: dict[str, Any] | None = None, ladder: tuple[str, ...] = LADDER) -> Route:
    """The model for a task of ``level`` given the measured ``profiles`` and the memory available right now."""
    if type(level) is not int or level < 1:
        return Route(None, "SLICE_CAPABILITY_LEVEL_UNDECLARED", None)
    qualified = qualified_models(level, profiles, hardware=hardware, ladder=ladder)
    if not qualified:
        # Name the most informative gap: the model closest to the task (primary first), with its own fail-closed reason.
        reasons = [(name, _valid(profiles, name, level, hardware)) for name in ladder if _full(name) in profiles] or \
                  [(ladder[0], _valid(profiles, ladder[0], level, hardware))]
        name, why = reasons[0]
        return Route(None, f"NO_MODEL_QUALIFIES_FOR_LEVEL_{level}: {name}: {why}", level, available_gib=available_gib)
    if available_gib is None:  # memory unknown is not memory available
        return Route(None, "AVAILABLE_MEMORY_UNKNOWN", level, qualified=tuple(qualified))
    needs = {name: required_gib(name) for name in qualified}
    admitted = [name for name in qualified if available_gib >= needs[name]]
    if not admitted:
        smallest = min(qualified, key=lambda n: needs[n])
        return Route(None, (f"WAITING_FOR_MEMORY: level {level} is covered only by {', '.join(qualified)}; "
                            f"{available_gib:.1f} GiB available < {needs[smallest]:.1f} GiB needed for the smallest, {smallest}"),
                     level, qualified=tuple(qualified), required_gib=needs[smallest], available_gib=available_gib)
    chosen = admitted[0]
    if chosen == ladder[0]:
        why = "PRIMARY_BUILDER"
    else:
        why = (f"CONSTRAINED_FALLBACK: {ladder[0]} is not available now ({available_gib:.1f} GiB free"
               f"{'' if ladder[0] not in qualified else f' < {needs[ladder[0]]:.1f} GiB needed'}); "
               f"{chosen} has demonstrated level {level}")
    return Route(chosen, why, level, qualified=tuple(qualified), admitted=tuple(admitted),
                 required_gib=needs[chosen], available_gib=available_gib)


def max_level_now(profiles: dict[str, dict[str, Any]], available_gib: float | None, *,
                  hardware: dict[str, Any] | None = None, ladder: tuple[str, ...] = LADDER) -> int:
    """The highest task level some model could take right now (0: no valid model fits in memory)."""
    if available_gib is None:
        return 0
    best = 0
    for name in ladder:
        if _valid(profiles, name, 1, hardware) is not None or available_gib < required_gib(name):
            continue
        best = max(best, int(profiles[_full(name)].get("max_passing_level") or 0))
    return best


def best_profile(profiles: dict[str, dict[str, Any]], *, hardware: dict[str, Any] | None = None,
                 ladder: tuple[str, ...] = LADDER) -> dict[str, Any] | None:
    """The valid profile with the highest measured level (primary first on ties): "can any local model ever do this?"."""
    valid = [profiles[_full(name)] for name in ladder if _valid(profiles, name, 1, hardware) is None]
    return max(valid, key=lambda p: int(p.get("max_passing_level") or 0), default=None)  # max keeps the first of equals
