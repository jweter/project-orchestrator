from __future__ import annotations

from typing import Any

from .actor_metrics import summarize


def actor_experiment_metrics(
    baseline_events: list[dict[str, Any]],
    post_events: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    baseline = summarize(baseline_events)
    post = summarize(post_events)
    return {
        "green_bridge_cycles": _higher_is_better(
            baseline["green_bridge_cycles"], post["green_bridge_cycles"]
        ),
        "cooldown_count": _lower_is_better(
            baseline["cooldown_count"], post["cooldown_count"]
        ),
    }


def _higher_is_better(before: int, after: int) -> dict[str, Any]:
    return {
        "status": "MEASURED",
        "baseline": before,
        "post_change": after,
        "outcome": "IMPROVED" if after > before else ("UNCHANGED" if after == before else "DEGRADED"),
    }


def _lower_is_better(before: int, after: int) -> dict[str, Any]:
    return {
        "status": "MEASURED",
        "baseline": before,
        "post_change": after,
        "outcome": "IMPROVED" if after < before else ("UNCHANGED" if after == before else "DEGRADED"),
    }
