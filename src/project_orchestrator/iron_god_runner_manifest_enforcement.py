"""Runner manifest enforcement (roadmap section 4.2: Runner
Authority; Phase 7: Event-Driven Scheduler).

"Runner selection must remain unchanged from planning through
execution... The worker must prove that it executed under that same
authority." Enforces an exact match between the runner requirement
tags a SliceSpec (iron_god_slicespec_generator.py, IG-066) declared
at planning time and what a worker reports it executed under -- not
a superset, not a subset. Any drift (missing or extra tags) is a
VIOLATION, fail-closed, since the roadmap specifically names
"planner selecting Claude while another runner executes" as the
failure this exists to prevent. This module performs no execution or
scheduling itself; it only compares an already-declared manifest
against an already-reported execution fact.
"""

from __future__ import annotations

from dataclasses import dataclass

from .iron_god_slicespec_generator import SliceSpec

CONFORMS = "CONFORMS"
VIOLATION = "VIOLATION"


@dataclass(frozen=True)
class RunnerEnforcementDecision:
    status: str
    declared_runner_requirements: tuple[str, ...]
    executed_runner_tags: tuple[str, ...]
    missing_tags: tuple[str, ...]
    unexpected_tags: tuple[str, ...]
    reason: str


def enforce_runner_manifest(
    spec: SliceSpec,
    executed_runner_tags: tuple[str, ...],
) -> RunnerEnforcementDecision:
    """Compare `spec.runner_requirements` against what was actually
    executed under. Requires an exact set match; any missing or
    unexpected tag is a VIOLATION."""
    declared = set(spec.runner_requirements)
    executed = set(executed_runner_tags)
    missing = tuple(sorted(declared - executed))
    unexpected = tuple(sorted(executed - declared))

    if missing or unexpected:
        return RunnerEnforcementDecision(
            status=VIOLATION,
            declared_runner_requirements=spec.runner_requirements,
            executed_runner_tags=executed_runner_tags,
            missing_tags=missing,
            unexpected_tags=unexpected,
            reason=(
                "executed runner tags do not exactly match the SliceSpec's "
                "declared runner_requirements"
            ),
        )

    return RunnerEnforcementDecision(
        status=CONFORMS,
        declared_runner_requirements=spec.runner_requirements,
        executed_runner_tags=executed_runner_tags,
        missing_tags=(),
        unexpected_tags=(),
        reason="executed runner tags exactly match declared runner_requirements",
    )
