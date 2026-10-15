"""Roadmap parser (roadmap section 3.1 / Phase 6: Capability Compiler).

Parses the free-text Project Roadmap document into structured,
read-only RoadmapPhase/ParsedRoadmap data (phase number, title,
deliverables as (IG-id, description) pairs, exit criteria text) for
later Capability Compiler stages (IG-062 onward) to consume without
re-parsing prose. Phase blocks are delimited by the document's own
section-separator line.

Surveyed against the real Roadmap.txt before writing this: not every
phase has a "Deliverables" section (Phase 9 and 10 do not) and not
every phase has an "Exit criteria" section (Phase 10 does not) --
both are legitimate and represented as an empty tuple / None, never
raised as an error. The only hard failures are genuine ambiguity: a
"Deliverables" header present with zero recognized "IG-0xx --
description" lines beneath it, a duplicate deliverable id across two
different phases, or a duplicate phase number. This module performs
no file or state mutation itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SEPARATOR = "⸻"  # "⸻", the document's own section separator
_PHASE_HEADER_RE = re.compile(r"^Phase\s+(\d+)\s+—\s+(.+)$")
_DELIVERABLE_RE = re.compile(r"^(IG-\d+)\s+—\s+(.+)$")
_DELIVERABLES_LABEL = "Deliverables"
_EXIT_CRITERIA_LABEL = "Exit criteria"


class RoadmapParseError(RuntimeError):
    """Raised when a roadmap block is ambiguous rather than well-formed."""


@dataclass(frozen=True)
class RoadmapPhase:
    phase_number: int
    title: str
    deliverables: tuple[tuple[str, str], ...]
    exit_criteria: str | None


@dataclass(frozen=True)
class ParsedRoadmap:
    phases: tuple[RoadmapPhase, ...]


def _find_phase_header(lines: list[str]) -> tuple[int, str] | None:
    for line in lines:
        match = _PHASE_HEADER_RE.match(line.strip())
        if match:
            return int(match.group(1)), match.group(2).strip()
    return None


def _extract_deliverables(lines: list[str]) -> tuple[tuple[str, str], ...]:
    try:
        start = next(
            i for i, line in enumerate(lines) if line.strip() == _DELIVERABLES_LABEL
        )
    except StopIteration:
        return ()

    deliverables: list[tuple[str, str]] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if not stripped:
            if deliverables:
                break
            continue
        match = _DELIVERABLE_RE.match(stripped)
        if match:
            deliverables.append((match.group(1), match.group(2).strip()))
        elif deliverables:
            break
    return tuple(deliverables)


def _extract_exit_criteria(lines: list[str]) -> str | None:
    try:
        start = next(
            i for i, line in enumerate(lines) if line.strip() == _EXIT_CRITERIA_LABEL
        )
    except StopIteration:
        return None

    collected = [line.strip() for line in lines[start + 1 :] if line.strip()]
    return " ".join(collected) if collected else None


def parse_roadmap(text: str) -> ParsedRoadmap:
    """Parse the free-text Project Roadmap document into phases.

    Raises RoadmapParseError when:
    * a block declares "Deliverables" but yields zero recognized
      "IG-0xx — description" lines,
    * a deliverable id is seen in more than one phase block,
    * two blocks claim the same phase number.

    A block with no "Deliverables" section parses with an empty
    deliverables tuple. A block with no "Exit criteria" section
    parses with exit_criteria=None. Neither is an error -- both occur
    in the real document (Phase 9 and Phase 10).
    """
    phases: list[RoadmapPhase] = []
    deliverable_owner: dict[str, int] = {}
    phase_number_owner: dict[int, str] = {}

    for block in text.split(_SEPARATOR):
        lines = block.splitlines()
        header = _find_phase_header(lines)
        if header is None:
            continue
        phase_number, title = header

        if phase_number in phase_number_owner:
            raise RoadmapParseError(
                f"phase number {phase_number} appears in both "
                f"{phase_number_owner[phase_number]!r} and {title!r}"
            )
        phase_number_owner[phase_number] = title

        has_deliverables_header = any(
            line.strip() == _DELIVERABLES_LABEL for line in lines
        )
        deliverables = _extract_deliverables(lines)
        if has_deliverables_header and not deliverables:
            raise RoadmapParseError(
                f"Phase {phase_number} ({title!r}) declares {_DELIVERABLES_LABEL!r} "
                "but no recognized IG-0xx — description lines were found"
            )

        for deliverable_id, _ in deliverables:
            if deliverable_id in deliverable_owner:
                raise RoadmapParseError(
                    f"{deliverable_id} appears in both Phase "
                    f"{deliverable_owner[deliverable_id]} and Phase {phase_number}"
                )
            deliverable_owner[deliverable_id] = phase_number

        phases.append(
            RoadmapPhase(
                phase_number=phase_number,
                title=title,
                deliverables=deliverables,
                exit_criteria=_extract_exit_criteria(lines),
            )
        )

    return ParsedRoadmap(phases=tuple(phases))
